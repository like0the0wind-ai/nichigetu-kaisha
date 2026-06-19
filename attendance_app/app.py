import os
from datetime import datetime, date, timezone, timedelta
from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

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
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS breaks (
                id         SERIAL PRIMARY KEY,
                record_id  INTEGER NOT NULL,
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
            CREATE TABLE IF NOT EXISTS employees (
                id                 SERIAL PRIMARY KEY,
                name               TEXT NOT NULL UNIQUE,
                hourly_rate        INTEGER NOT NULL DEFAULT 0,
                transport_allowance INTEGER NOT NULL DEFAULT 0,
                other_allowance    INTEGER NOT NULL DEFAULT 0,
                notes              TEXT
            )
        """ if DATABASE_URL else """
            CREATE TABLE IF NOT EXISTS employees (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL UNIQUE,
                hourly_rate         INTEGER NOT NULL DEFAULT 0,
                transport_allowance INTEGER NOT NULL DEFAULT 0,
                other_allowance     INTEGER NOT NULL DEFAULT 0,
                notes               TEXT
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS payslips (
                id                  SERIAL PRIMARY KEY,
                employee_name       TEXT NOT NULL,
                period_start        TEXT NOT NULL,
                period_end          TEXT NOT NULL,
                total_work_min      INTEGER NOT NULL DEFAULT 0,
                regular_min         INTEGER NOT NULL DEFAULT 0,
                overtime_min        INTEGER NOT NULL DEFAULT 0,
                base_pay            INTEGER NOT NULL DEFAULT 0,
                overtime_pay        INTEGER NOT NULL DEFAULT 0,
                transport_allowance INTEGER NOT NULL DEFAULT 0,
                other_allowance     INTEGER NOT NULL DEFAULT 0,
                gross_pay           INTEGER NOT NULL DEFAULT 0,
                health_insurance    INTEGER NOT NULL DEFAULT 0,
                pension             INTEGER NOT NULL DEFAULT 0,
                employment_insurance INTEGER NOT NULL DEFAULT 0,
                income_tax          INTEGER NOT NULL DEFAULT 0,
                other_deduction     INTEGER NOT NULL DEFAULT 0,
                total_deduction     INTEGER NOT NULL DEFAULT 0,
                net_pay             INTEGER NOT NULL DEFAULT 0,
                note                TEXT,
                created_at          TEXT NOT NULL
            )
        """ if DATABASE_URL else """
            CREATE TABLE IF NOT EXISTS payslips (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name       TEXT NOT NULL,
                period_start        TEXT NOT NULL,
                period_end          TEXT NOT NULL,
                total_work_min      INTEGER NOT NULL DEFAULT 0,
                regular_min         INTEGER NOT NULL DEFAULT 0,
                overtime_min        INTEGER NOT NULL DEFAULT 0,
                base_pay            INTEGER NOT NULL DEFAULT 0,
                overtime_pay        INTEGER NOT NULL DEFAULT 0,
                transport_allowance INTEGER NOT NULL DEFAULT 0,
                other_allowance     INTEGER NOT NULL DEFAULT 0,
                gross_pay           INTEGER NOT NULL DEFAULT 0,
                health_insurance    INTEGER NOT NULL DEFAULT 0,
                pension             INTEGER NOT NULL DEFAULT 0,
                employment_insurance INTEGER NOT NULL DEFAULT 0,
                income_tax          INTEGER NOT NULL DEFAULT 0,
                other_deduction     INTEGER NOT NULL DEFAULT 0,
                total_deduction     INTEGER NOT NULL DEFAULT 0,
                net_pay             INTEGER NOT NULL DEFAULT 0,
                note                TEXT,
                created_at          TEXT NOT NULL
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
                        (name, now_jst()))
                msg = f"{name} さんの出勤を記録しました ✓"
        elif action == "clock_out":
            rows = query("SELECT id FROM records WHERE name=? AND clock_out IS NULL", (name,))
            if not rows:
                msg = f"{name} さんの出勤記録が見つかりません"
            else:
                rec_id = rows[0]["id"]
                # 休憩中なら自動終了
                br = query("SELECT id FROM breaks WHERE record_id=? AND break_end IS NULL", (rec_id,))
                if br:
                    execute("UPDATE breaks SET break_end=? WHERE id=?", (now_jst(), br[0]["id"]))
                # break_min を集計して更新
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
        break_min = r["break_min"] or 0
        work_min = int((co - ci).total_seconds() // 60) - break_min if co else None
        records.append({
            "id": r["id"], "name": r["name"],
            "date": ci.strftime("%m/%d"),
            "clock_in": ci.strftime("%H:%M"),
            "clock_out": co.strftime("%H:%M") if co else "—",
            "work": f"{work_min // 60}h{work_min % 60:02d}m" if work_min is not None else "出勤中",
            "break": f"{break_min // 60}h{break_min % 60:02d}m" if break_min > 0 else "—",
        })
        if r["name"] not in staff:
            staff[r["name"]] = {"days": 0, "total_min": 0, "break_min": 0}
        staff[r["name"]]["days"] += 1
        staff[r["name"]]["total_min"] += work_min or 0
        staff[r["name"]]["break_min"] += break_min

    staff_summary = [{"name": n, "days": s["days"],
        "total": f"{s['total_min']//60}h{s['total_min']%60:02d}m",
        "break": f"{s['break_min']//60}h{s['break_min']%60:02d}m" if s["break_min"] > 0 else "—"}
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

# ── 給与管理 ──────────────────────────────────────────────────────

def calc_payslip_data(emp, records_for_period):
    """勤怠レコードから給与を計算して辞書を返す"""
    hourly = emp["hourly_rate"]
    daily_regular_min = 8 * 60  # 1日8時間が法定労働時間

    total_work_min = 0
    regular_min = 0
    overtime_min = 0

    for r in records_for_period:
        ci = datetime.strptime(r["clock_in"], "%Y-%m-%d %H:%M:%S")
        co = datetime.strptime(r["clock_out"], "%Y-%m-%d %H:%M:%S") if r["clock_out"] else None
        if co is None:
            continue
        work_min = int((co - ci).total_seconds() // 60) - (r["break_min"] or 0)
        if work_min <= 0:
            continue
        total_work_min += work_min
        reg = min(work_min, daily_regular_min)
        ot  = max(0, work_min - daily_regular_min)
        regular_min  += reg
        overtime_min += ot

    base_pay     = int(hourly * regular_min / 60)
    overtime_pay = int(hourly * 1.25 * overtime_min / 60)
    transport    = emp["transport_allowance"]
    other_allow  = emp["other_allowance"]
    gross_pay    = base_pay + overtime_pay + transport + other_allow

    return {
        "total_work_min": total_work_min,
        "regular_min":    regular_min,
        "overtime_min":   overtime_min,
        "base_pay":       base_pay,
        "overtime_pay":   overtime_pay,
        "transport_allowance": transport,
        "other_allowance":     other_allow,
        "gross_pay":      gross_pay,
    }

@app.route("/admin/payroll")
def admin_payroll():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    default_start, default_end = current_pay_period()
    date_from = request.args.get("from", default_start.isoformat())
    date_to   = request.args.get("to",   default_end.isoformat())

    employees = query("SELECT * FROM employees ORDER BY name")
    payslips  = query(
        "SELECT * FROM payslips WHERE period_start=? AND period_end=? ORDER BY employee_name",
        (date_from, date_to)
    )
    issued_names = {p["employee_name"] for p in payslips}

    # 期間内の勤怠サマリー（従業員ごと）
    records = query(
        "SELECT * FROM records WHERE date(clock_in) BETWEEN ? AND ? AND clock_out IS NOT NULL",
        (date_from, date_to)
    )
    summary = {}
    for r in records:
        n = r["name"]
        ci = datetime.strptime(r["clock_in"], "%Y-%m-%d %H:%M:%S")
        co = datetime.strptime(r["clock_out"], "%Y-%m-%d %H:%M:%S")
        work_min = int((co - ci).total_seconds() // 60) - (r["break_min"] or 0)
        if n not in summary:
            summary[n] = {"days": 0, "total_min": 0}
        summary[n]["days"] += 1
        summary[n]["total_min"] += max(0, work_min)

    return render_template("payroll.html",
        employees=employees,
        payslips=payslips,
        issued_names=issued_names,
        summary=summary,
        date_from=date_from,
        date_to=date_to,
        period_label=pay_period_label(
            date.fromisoformat(date_from), date.fromisoformat(date_to))
    )

@app.route("/admin/payroll/employee/save", methods=["POST"])
def admin_employee_save():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    emp_id    = request.form.get("id", "").strip()
    name      = request.form["name"].strip()
    hourly    = int(request.form.get("hourly_rate", 0) or 0)
    transport = int(request.form.get("transport_allowance", 0) or 0)
    other     = int(request.form.get("other_allowance", 0) or 0)
    notes     = request.form.get("notes", "").strip()

    if emp_id:
        execute(
            "UPDATE employees SET name=?, hourly_rate=?, transport_allowance=?, other_allowance=?, notes=? WHERE id=?",
            (name, hourly, transport, other, notes, emp_id)
        )
    else:
        execute(
            "INSERT INTO employees (name, hourly_rate, transport_allowance, other_allowance, notes) VALUES (?,?,?,?,?)",
            (name, hourly, transport, other, notes)
        )
    return redirect(url_for("admin_payroll"))

@app.route("/admin/payroll/employee/<int:emp_id>/delete", methods=["POST"])
def admin_employee_delete(emp_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    execute("DELETE FROM employees WHERE id=?", (emp_id,))
    return redirect(url_for("admin_payroll"))

@app.route("/admin/payroll/generate", methods=["POST"])
def admin_payslip_generate():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    emp_name   = request.form["employee_name"]
    date_from  = request.form["date_from"]
    date_to    = request.form["date_to"]
    note       = request.form.get("note", "").strip()

    # 従業員設定取得
    emps = query("SELECT * FROM employees WHERE name=?", (emp_name,))
    if not emps:
        return redirect(url_for("admin_payroll"))
    emp = emps[0]

    # 勤怠取得
    records = query(
        "SELECT * FROM records WHERE name=? AND date(clock_in) BETWEEN ? AND ? AND clock_out IS NOT NULL",
        (emp_name, date_from, date_to)
    )

    data = calc_payslip_data(emp, records)

    # 控除（フォームから手動入力）
    health   = int(request.form.get("health_insurance", 0) or 0)
    pension  = int(request.form.get("pension", 0) or 0)
    emp_ins  = int(request.form.get("employment_insurance", 0) or 0)
    tax      = int(request.form.get("income_tax", 0) or 0)
    other_d  = int(request.form.get("other_deduction", 0) or 0)
    total_d  = health + pension + emp_ins + tax + other_d
    net_pay  = data["gross_pay"] - total_d

    # 既存の明細があれば削除して再発行
    execute(
        "DELETE FROM payslips WHERE employee_name=? AND period_start=? AND period_end=?",
        (emp_name, date_from, date_to)
    )
    execute("""
        INSERT INTO payslips (
            employee_name, period_start, period_end,
            total_work_min, regular_min, overtime_min,
            base_pay, overtime_pay, transport_allowance, other_allowance, gross_pay,
            health_insurance, pension, employment_insurance, income_tax, other_deduction,
            total_deduction, net_pay, note, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        emp_name, date_from, date_to,
        data["total_work_min"], data["regular_min"], data["overtime_min"],
        data["base_pay"], data["overtime_pay"],
        data["transport_allowance"], data["other_allowance"], data["gross_pay"],
        health, pension, emp_ins, tax, other_d,
        total_d, net_pay, note, now_jst()
    ))

    slip = query(
        "SELECT id FROM payslips WHERE employee_name=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1",
        (emp_name, date_from, date_to)
    )
    return redirect(url_for("admin_payslip_view", slip_id=slip[0]["id"]))

@app.route("/admin/payroll/preview", methods=["POST"])
def admin_payslip_preview():
    """明細プレビュー（保存なし）用JSON"""
    if not session.get("admin"):
        return jsonify({}), 403

    emp_name  = request.json.get("employee_name")
    date_from = request.json.get("date_from")
    date_to   = request.json.get("date_to")

    emps = query("SELECT * FROM employees WHERE name=?", (emp_name,))
    if not emps:
        return jsonify({"error": "従業員が見つかりません"})
    emp = emps[0]

    records = query(
        "SELECT * FROM records WHERE name=? AND date(clock_in) BETWEEN ? AND ? AND clock_out IS NOT NULL",
        (emp_name, date_from, date_to)
    )
    data = calc_payslip_data(emp, records)
    return jsonify(data)

@app.route("/admin/payslip/<int:slip_id>")
def admin_payslip_view(slip_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    slips = query("SELECT * FROM payslips WHERE id=?", (slip_id,))
    if not slips:
        return redirect(url_for("admin_payroll"))
    slip = slips[0]
    return render_template("payslip.html", slip=slip)

@app.route("/admin/payslip/<int:slip_id>/delete", methods=["POST"])
def admin_payslip_delete(slip_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    execute("DELETE FROM payslips WHERE id=?", (slip_id,))
    return redirect(url_for("admin_payroll"))

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
