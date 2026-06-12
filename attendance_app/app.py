import os
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, session

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nichigetsu-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "saito")

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── DB ────────────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        import psycopg2, psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        url = url.split("?")[0]  # pgbouncer=true等のパラメータを除去
        conn = psycopg2.connect(url)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "attendance.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

def ph():
    return "%s" if DATABASE_URL else "?"

def init_db():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS records (
                id        SERIAL PRIMARY KEY,
                name      TEXT NOT NULL,
                clock_in  TEXT NOT NULL,
                clock_out TEXT,
                break_min INTEGER DEFAULT 0
            )
        """ if DATABASE_URL else """
            CREATE TABLE IF NOT EXISTS records (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT NOT NULL,
                clock_in  TEXT NOT NULL,
                clock_out TEXT,
                break_min INTEGER DEFAULT 0
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS breaks (
                id          SERIAL PRIMARY KEY,
                record_id   INTEGER NOT NULL,
                break_start TEXT NOT NULL,
                break_end   TEXT
            )
        """ if DATABASE_URL else """
            CREATE TABLE IF NOT EXISTS breaks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id   INTEGER NOT NULL,
                break_start TEXT NOT NULL,
                break_end   TEXT
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS staff (
                id   SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            )
        """ if DATABASE_URL else """
            CREATE TABLE IF NOT EXISTS staff (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        conn.commit()
    finally:
        conn.close()

def query(sql, params=()):
    p = ph()
    sql = sql.replace("?", p)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        try:
            return cur.fetchall()
        except Exception:
            return []
    finally:
        conn.close()

def execute(sql, params=()):
    p = ph()
    sql = sql.replace("?", p)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()

STANDARD_HOURS = 6  # 基本労働時間

def ceil15(minutes):
    """15分単位で切り上げ（出勤用）"""
    import math
    return math.ceil(minutes / 15) * 15

def floor15(minutes):
    """15分単位で切り捨て（退勤用）"""
    return (minutes // 15) * 15

def fmt_time(minutes):
    """時間表示（m省略）: 6h / 6h15 / 0h30"""
    h = minutes // 60
    m = minutes % 60
    if m == 0:
        return f"{h}h"
    return f"{h}h{m:02d}"

# ── 従業員側 ──────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def index():
    msg = None
    if request.method == "POST":
        action = request.form["action"]
        name   = request.form["name"].strip()
        if not name:
            msg = "名前を選択してください"
        elif action == "clock_in":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if rows:
                msg = f"{name} さんはすでに出勤中です"
            else:
                execute("INSERT INTO records (name, clock_in) VALUES (?, ?)", (name, now_jst()))
                msg = f"{name} さんの出勤を記録しました ✓"
        elif action == "clock_out":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if not rows:
                msg = f"{name} さんの出勤記録が見つかりません"
            else:
                rec_id = rows[0]["id"]
                br = query("SELECT id FROM breaks WHERE record_id=? AND break_end IS NULL", (rec_id,))
                if br:
                    execute("UPDATE breaks SET break_end=? WHERE id=?", (now_jst(), br[0]["id"]))
                brs = query("SELECT break_start, break_end FROM breaks WHERE record_id=?", (rec_id,))
                total_break = 0
                for b in brs:
                    if b["break_end"]:
                        s = datetime.strptime(b["break_start"], "%Y-%m-%d %H:%M:%S")
                        e = datetime.strptime(b["break_end"],   "%Y-%m-%d %H:%M:%S")
                        total_break += int((e - s).total_seconds() // 60)
                execute("UPDATE records SET clock_out=?, break_min=? WHERE id=?",
                        (now_jst(), total_break, rec_id))
                msg = f"{name} さんの退勤を記録しました ✓"
        elif action == "break_start":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if not rows:
                msg = f"{name} さんの出勤記録が見つかりません"
            else:
                rec_id = rows[0]["id"]
                br = query("SELECT id FROM breaks WHERE record_id=? AND break_end IS NULL", (rec_id,))
                if br:
                    msg = f"{name} さんはすでに休憩中です"
                else:
                    execute("INSERT INTO breaks (record_id, break_start) VALUES (?, ?)",
                            (rec_id, now_jst()))
                    msg = f"{name} さんの休憩開始を記録しました ✓"
        elif action == "break_end":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if not rows:
                msg = f"{name} さんの出勤記録が見つかりません"
            else:
                rec_id = rows[0]["id"]
                br = query("SELECT id FROM breaks WHERE record_id=? AND break_end IS NULL", (rec_id,))
                if not br:
                    msg = f"{name} さんは休憩中ではありません"
                else:
                    execute("UPDATE breaks SET break_end=? WHERE id=?", (now_jst(), br[0]["id"]))
                    msg = f"{name} さんの休憩終了を記録しました ✓"

    staff_rows = query("SELECT name FROM staff ORDER BY name")
    staff_status = []
    for s in staff_rows:
        n = s["name"]
        rec = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (n,))
        if rec:
            br = query("SELECT id FROM breaks WHERE record_id=? AND break_end IS NULL", (rec[0]["id"],))
            status = "break" if br else "in"
        else:
            status = "out"
        staff_status.append({"name": n, "status": status})

    return render_template("index.html", staff_status=staff_status, msg=msg)

# ── 給与期間 ──────────────────────────────────────────────────────

def current_pay_period(today=None):
    t = today or date.today()
    if t.day >= 11:
        return t.replace(day=11), (t + relativedelta(months=1)).replace(day=10)
    return (t - relativedelta(months=1)).replace(day=11), t.replace(day=10)

def pay_period_label(start, end):
    return f"{start.month}月{start.day}日〜{end.month}月{end.day}日"

# ── 管理者側 ──────────────────────────────────────────────────────

@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    default_start, default_end = current_pay_period()
    date_from = request.args.get("from", default_start.isoformat())
    date_to   = request.args.get("to",   default_end.isoformat())

    rows = query(
        "SELECT * FROM records WHERE date(clock_in) BETWEEN ? AND ? ORDER BY clock_in",
        (date_from, date_to)
    )

    staff, records = {}, []
    for r in rows:
        ci = datetime.strptime(r["clock_in"], "%Y-%m-%d %H:%M:%S")
        co = datetime.strptime(r["clock_out"], "%Y-%m-%d %H:%M:%S") if r["clock_out"] else None
        break_min = r["break_min"] or 0

        if co:
            # 出勤: 15分切り上げ、退勤: 15分切り下げ
            ci_min = ceil15(ci.hour * 60 + ci.minute)
            co_min = floor15(co.hour * 60 + co.minute)
            total_min = max(0, co_min - ci_min - break_min)
            std_min = min(total_min, STANDARD_HOURS * 60)
            over_min = floor15(max(0, total_min - STANDARD_HOURS * 60))
        else:
            std_min = None
            over_min = None
            total_min = 0

        records.append({
            "id": r["id"], "name": r["name"],
            "date": ci.strftime("%m/%d"),
            "clock_in": ci.strftime("%H:%M"),
            "clock_out": co.strftime("%H:%M") if co else "—",
            "work": fmt_time(std_min) if std_min is not None else "出勤中",
            "overtime": fmt_time(over_min) if over_min is not None and over_min > 0 else "—",
            "break": fmt_time(break_min) if break_min > 0 else "—",
            "work_min": std_min or 0,
            "over_min": over_min or 0,
        })
        if r["name"] not in staff:
            staff[r["name"]] = {"days": 0, "total_min": 0, "over_min": 0, "break_min": 0}
        staff[r["name"]]["days"] += 1
        staff[r["name"]]["total_min"] += std_min or 0
        staff[r["name"]]["over_min"] += over_min or 0
        staff[r["name"]]["break_min"] += break_min

    staff_summary = [{"name": n, "days": s["days"],
        "total": fmt_time(s["total_min"]),
        "overtime": fmt_time(s["over_min"]) if s["over_min"] > 0 else "—",
        "break": fmt_time(s["break_min"]) if s["break_min"] > 0 else "—"}
        for n, s in sorted(staff.items())]

    # 日別グループ
    days_dict = defaultdict(lambda: {"records": [], "total_min": 0, "over_min": 0})
    for r in records:
        days_dict[r["date"]]["records"].append(r)
        days_dict[r["date"]]["total_min"] += r["work_min"]
        days_dict[r["date"]]["over_min"] += r["over_min"]
    days = [
        {"date": d, "records": info["records"],
         "total": fmt_time(info["total_min"]),
         "overtime": fmt_time(info["over_min"]) if info["over_min"] > 0 else ""}
        for d, info in sorted(days_dict.items())
    ]

    return render_template("admin.html",
        staff_summary=staff_summary, days=days,
        date_from=date_from, date_to=date_to,
        period_label=pay_period_label(
            date.fromisoformat(date_from), date.fromisoformat(date_to)))

@app.route("/admin/staff", methods=["GET", "POST"])
def admin_staff():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        name = request.form.get("name", "").strip()
        if action == "add" and name:
            try:
                execute("INSERT INTO staff (name) VALUES (?)", (name,))
            except Exception:
                error = f"「{name}」はすでに登録されています"
        elif action == "delete" and name:
            execute("DELETE FROM staff WHERE name=?", (name,))
    staff = query("SELECT * FROM staff ORDER BY name")
    return render_template("staff.html", staff=staff, error=error)

@app.route("/admin/delete/<int:rec_id>", methods=["POST"])
def admin_delete(rec_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    execute("DELETE FROM records WHERE id=?", (rec_id,))
    return redirect(request.referrer or url_for("admin"))

@app.route("/admin/edit/<int:rec_id>", methods=["GET", "POST"])
def admin_edit(rec_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    rows = query("SELECT * FROM records WHERE id=?", (rec_id,))
    if not rows:
        return redirect(url_for("admin"))
    rec = rows[0]

    error = None
    if request.method == "POST":
        name      = request.form["name"].strip()
        clock_in  = request.form["clock_in"].strip()
        clock_out = request.form["clock_out"].strip()
        try:
            ci = datetime.strptime(clock_in, "%Y-%m-%dT%H:%M")
            co = datetime.strptime(clock_out, "%Y-%m-%dT%H:%M") if clock_out else None
            if co and co < ci:
                raise ValueError("退勤が出勤より早い")
            execute(
                "UPDATE records SET name=?, clock_in=?, clock_out=? WHERE id=?",
                (name,
                 ci.strftime("%Y-%m-%d %H:%M:%S"),
                 co.strftime("%Y-%m-%d %H:%M:%S") if co else None,
                 rec_id)
            )
            return redirect(url_for("admin"))
        except ValueError as e:
            error = f"入力エラー: {e}"

    ci_val = rec["clock_in"][:16].replace(" ", "T") if rec["clock_in"] else ""
    co_val = rec["clock_out"][:16].replace(" ", "T") if rec["clock_out"] else ""

    return render_template("edit.html", rec=rec, ci_val=ci_val, co_val=co_val, error=error)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form["password"] == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        error = "パスワードが違います"
    return render_template("login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
