import os
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nichigetsu-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "saito")

DATABASE_URL = os.environ.get("DATABASE_URL")  # Renderが自動設定

# ── DB ────────────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        import psycopg2, psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
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
    """プレースホルダー: PostgreSQL は %s、SQLite は ?"""
    return "%s" if DATABASE_URL else "?"

def init_db():
    with get_db() as conn:
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
        conn.commit()

def query(sql, params=()):
    p = ph()
    sql = sql.replace("?", p)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        try:
            return cur.fetchall()
        except Exception:
            return []

def execute(sql, params=()):
    p = ph()
    sql = sql.replace("?", p)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()

# ── 従業員側 ──────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def index():
    msg = None
    if request.method == "POST":
        action = request.form["action"]
        name   = request.form["name"].strip()
        if not name:
            msg = "名前を入力してください"
        elif action == "clock_in":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if rows:
                msg = f"{name} さんはすでに出勤中です"
            else:
                execute("INSERT INTO records (name, clock_in) VALUES (?, ?)",
                        (name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                msg = f"{name} さんの出勤を記録しました ✓"
        elif action == "clock_out":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if not rows:
                msg = f"{name} さんの出勤記録が見つかりません"
            else:
                execute("UPDATE records SET clock_out=? WHERE id=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rows[0]["id"]))
                msg = f"{name} さんの退勤を記録しました ✓"
    return render_template("index.html", msg=msg)

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
        work_min = int((co - ci).total_seconds() // 60) - (r["break_min"] or 0) if co else None
        records.append({
            "id": r["id"], "name": r["name"],
            "date": ci.strftime("%m/%d"),
            "clock_in": ci.strftime("%H:%M"),
            "clock_out": co.strftime("%H:%M") if co else "—",
            "work": f"{work_min // 60}h{work_min % 60:02d}m" if work_min is not None else "出勤中",
        })
        if r["name"] not in staff:
            staff[r["name"]] = {"days": 0, "total_min": 0}
        staff[r["name"]]["days"] += 1
        staff[r["name"]]["total_min"] += work_min or 0

    staff_summary = [{"name": n, "days": s["days"],
        "total": f"{s['total_min']//60}h{s['total_min']%60:02d}m"}
        for n, s in sorted(staff.items())]

    return render_template("admin.html",
        records=records, staff_summary=staff_summary,
        date_from=date_from, date_to=date_to,
        period_label=pay_period_label(
            date.fromisoformat(date_from), date.fromisoformat(date_to)))

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
            # バリデーション
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

    # datetime-local 形式に変換
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
