import io
import os
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, session, send_file

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def today_jst():
    return datetime.now(JST).strftime("%Y-%m-%d")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nichigetsu-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "saito")

DATABASE_URL = os.environ.get("DATABASE_URL")

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
    return "%s" if DATABASE_URL else "?"

def init_db():
    conn = get_db()
    try:
        cur = conn.cursor()
        serial = "SERIAL" if DATABASE_URL else "INTEGER"
        ai     = "" if DATABASE_URL else "AUTOINCREMENT"

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS records (
                id        {serial} PRIMARY KEY {ai},
                name      TEXT NOT NULL,
                clock_in  TEXT NOT NULL,
                clock_out TEXT,
                break_min INTEGER DEFAULT 0
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS breaks (
                id          {serial} PRIMARY KEY {ai},
                record_id   INTEGER NOT NULL,
                break_start TEXT NOT NULL,
                break_end   TEXT
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS staff (
                id   {serial} PRIMARY KEY {ai},
                name TEXT NOT NULL UNIQUE
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS sales (
                id       {serial} PRIMARY KEY {ai},
                date     TEXT NOT NULL,
                amount   INTEGER NOT NULL,
                category TEXT DEFAULT '',
                memo     TEXT DEFAULT ''
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS shifts (
                id         {serial} PRIMARY KEY {ai},
                name       TEXT NOT NULL,
                date       TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time   TEXT NOT NULL
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS products (
                id       {serial} PRIMARY KEY {ai},
                name     TEXT NOT NULL UNIQUE,
                category TEXT DEFAULT '',
                price    INTEGER DEFAULT 0,
                cost     INTEGER DEFAULT 0,
                recipe   TEXT DEFAULT ''
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

STANDARD_HOURS = 6

def ceil15(minutes):
    import math
    return math.ceil(minutes / 15) * 15

def floor15(minutes):
    return (minutes // 15) * 15

def fmt_time(minutes):
    h = minutes // 60
    m = minutes % 60
    if m == 0:
        return f"{h}h"
    return f"{h}h{m:02d}"

# ── 従業員側（打刻） ──────────────────────────────────────────────

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
    return render_template("index.html", staff_rows=staff_rows, msg=msg)

# ── 給与期間 ──────────────────────────────────────────────────────

def current_pay_period(today=None):
    t = today or date.today()
    if t.day >= 11:
        return t.replace(day=11), (t + relativedelta(months=1)).replace(day=10)
    return (t - relativedelta(months=1)).replace(day=11), t.replace(day=10)

def pay_period_label(start, end):
    return f"{start.month}月{start.day}日〜{end.month}月{end.day}日"

# ── 管理者側 ──────────────────────────────────────────────────────

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/admin")
@admin_required
def admin():
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
            ci_min = ceil15(ci.hour * 60 + ci.minute)
            co_min = floor15(co.hour * 60 + co.minute)
            total_min = max(0, co_min - ci_min - break_min)
            std_min  = min(total_min, STANDARD_HOURS * 60)
            over_min = floor15(max(0, total_min - STANDARD_HOURS * 60))
        else:
            std_min = over_min = None
            total_min = 0

        records.append({
            "id": r["id"], "name": r["name"],
            "date": ci.strftime("%m/%d"),
            "clock_in": ci.strftime("%H:%M"),
            "clock_out": co.strftime("%H:%M") if co else "—",
            "work": fmt_time(std_min) if std_min is not None else "出勤中",
            "overtime": fmt_time(over_min) if over_min else "—",
            "break": fmt_time(break_min) if break_min else "—",
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
        "overtime": fmt_time(s["over_min"]) if s["over_min"] else "—",
        "break": fmt_time(s["break_min"]) if s["break_min"] else "—",
        "total_min": s["total_min"], "over_min": s["over_min"]}
        for n, s in sorted(staff.items())]

    days_dict = defaultdict(lambda: {"records": [], "total_min": 0})
    for r in records:
        days_dict[r["date"]]["records"].append(r)
        days_dict[r["date"]]["total_min"] += r["work_min"]
    days = [{"date": d, "records": info["records"], "total": fmt_time(info["total_min"])}
            for d, info in sorted(days_dict.items())]

    return render_template("admin.html",
        staff_summary=staff_summary, days=days,
        date_from=date_from, date_to=date_to,
        period_label=pay_period_label(
            date.fromisoformat(date_from), date.fromisoformat(date_to)))

# ── スタッフ管理 ──────────────────────────────────────────────────

@app.route("/admin/staff", methods=["GET", "POST"])
@admin_required
def admin_staff():
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        name   = request.form.get("name", "").strip()
        if action == "add" and name:
            try:
                execute("INSERT INTO staff (name) VALUES (?)", (name,))
            except Exception:
                error = f"「{name}」はすでに登録されています"
        elif action == "delete" and name:
            execute("DELETE FROM staff WHERE name=?", (name,))
    staff = query("SELECT * FROM staff ORDER BY name")
    return render_template("staff.html", staff=staff, error=error)

# ── 記録編集・削除 ────────────────────────────────────────────────

@app.route("/admin/delete/<int:rec_id>", methods=["POST"])
@admin_required
def admin_delete(rec_id):
    execute("DELETE FROM records WHERE id=?", (rec_id,))
    return redirect(request.referrer or url_for("admin"))

@app.route("/admin/edit/<int:rec_id>", methods=["GET", "POST"])
@admin_required
def admin_edit(rec_id):
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
            execute("UPDATE records SET name=?, clock_in=?, clock_out=? WHERE id=?",
                    (name,
                     ci.strftime("%Y-%m-%d %H:%M:%S"),
                     co.strftime("%Y-%m-%d %H:%M:%S") if co else None,
                     rec_id))
            return redirect(url_for("admin"))
        except ValueError as e:
            error = f"入力エラー: {e}"

    ci_val = rec["clock_in"][:16].replace(" ", "T") if rec["clock_in"] else ""
    co_val = rec["clock_out"][:16].replace(" ", "T") if rec["clock_out"] else ""
    return render_template("edit.html", rec=rec, ci_val=ci_val, co_val=co_val, error=error)

# ── 給与明細 Excel 出力 ───────────────────────────────────────────

@app.route("/admin/payslip")
@admin_required
def admin_payslip():
    staff_rows = query("SELECT name FROM staff ORDER BY name")
    default_start, default_end = current_pay_period()
    date_from = request.args.get("from", default_start.isoformat())
    date_to   = request.args.get("to",   default_end.isoformat())
    target    = request.args.get("name", "")
    download  = request.args.get("dl", "")

    results = []
    if target:
        rows = query(
            "SELECT * FROM records WHERE name=? AND date(clock_in) BETWEEN ? AND ? ORDER BY clock_in",
            (target, date_from, date_to)
        )
        for r in rows:
            ci = datetime.strptime(r["clock_in"], "%Y-%m-%d %H:%M:%S")
            co = datetime.strptime(r["clock_out"], "%Y-%m-%d %H:%M:%S") if r["clock_out"] else None
            break_min = r["break_min"] or 0
            if co:
                ci_min = ceil15(ci.hour * 60 + ci.minute)
                co_min = floor15(co.hour * 60 + co.minute)
                total  = max(0, co_min - ci_min - break_min)
                std    = min(total, STANDARD_HOURS * 60)
                over   = floor15(max(0, total - STANDARD_HOURS * 60))
            else:
                std = over = None
            results.append({
                "date": ci.strftime("%Y/%m/%d"),
                "clock_in": ci.strftime("%H:%M"),
                "clock_out": co.strftime("%H:%M") if co else "—",
                "break": fmt_time(break_min) if break_min else "—",
                "work": fmt_time(std) if std is not None else "出勤中",
                "overtime": fmt_time(over) if over else "—",
                "work_min": std or 0,
                "over_min": over or 0,
            })

        if download == "1" and results:
            return _make_payslip_excel(target, date_from, date_to, results)

    return render_template("payslip.html",
        staff_rows=staff_rows, target=target,
        date_from=date_from, date_to=date_to,
        results=results,
        period_label=pay_period_label(date.fromisoformat(date_from), date.fromisoformat(date_to)),
        total_work=fmt_time(sum(r["work_min"] for r in results)),
        total_over=fmt_time(sum(r["over_min"] for r in results)),
        days=len(results))

def _make_payslip_excel(name, date_from, date_to, results):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return "openpyxl が未インストールです。pip install openpyxl を実行してください。", 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "給与明細"

    brown = "C87941"
    light = "FAF5EE"
    thin  = Border(
        left=Side(style="thin", color="C8A878"),
        right=Side(style="thin", color="C8A878"),
        top=Side(style="thin", color="C8A878"),
        bottom=Side(style="thin", color="C8A878"),
    )

    ws.merge_cells("A1:F1")
    ws["A1"] = f"パン屋ニチゲツ　給与明細"
    ws["A1"].font = Font(bold=True, size=14, color="3A2010")
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A2:F2")
    ws["A2"] = f"氏名: {name}　　期間: {date_from} 〜 {date_to}"
    ws["A2"].font = Font(size=11, color="7A4010")
    ws["A2"].alignment = Alignment(horizontal="center")

    headers = ["日付", "出勤", "退勤", "休憩", "勤務時間", "残業"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=brown)
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin

    for i, r in enumerate(results, 5):
        vals = [r["date"], r["clock_in"], r["clock_out"], r["break"], r["work"], r["overtime"]]
        for col, v in enumerate(vals, 1):
            cell = ws.cell(row=i, column=col, value=v)
            cell.alignment = Alignment(horizontal="center")
            cell.border = thin
            if i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=light)

    total_row = len(results) + 5
    ws.cell(row=total_row, column=1, value="合計").font = Font(bold=True)
    ws.cell(row=total_row, column=4, value=f"{len(results)} 日").alignment = Alignment(horizontal="center")
    ws.cell(row=total_row, column=5, value=fmt_time(sum(r["work_min"] for r in results))).font = Font(bold=True, color=brown)
    ws.cell(row=total_row, column=6, value=fmt_time(sum(r["over_min"] for r in results))).font = Font(bold=True)
    for col in range(1, 7):
        ws.cell(row=total_row, column=col).border = thin

    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 14

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"給与明細_{name}_{date_from}_{date_to}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── 日次売上 ─────────────────────────────────────────────────────

@app.route("/admin/sales", methods=["GET", "POST"])
@admin_required
def admin_sales():
    error = msg = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            sale_date = request.form.get("date", today_jst())
            amount    = request.form.get("amount", "").strip()
            category  = request.form.get("category", "").strip()
            memo      = request.form.get("memo", "").strip()
            if not amount.isdigit():
                error = "金額は数字で入力してください"
            else:
                execute("INSERT INTO sales (date, amount, category, memo) VALUES (?, ?, ?, ?)",
                        (sale_date, int(amount), category, memo))
                msg = "売上を記録しました ✓"
        elif action == "delete":
            sale_id = request.form.get("sale_id")
            if sale_id:
                execute("DELETE FROM sales WHERE id=?", (sale_id,))

    month = request.args.get("month", today_jst()[:7])
    rows  = query("SELECT * FROM sales WHERE date LIKE ? ORDER BY date DESC", (f"{month}%",))
    total = sum(r["amount"] for r in rows)

    monthly = []
    seen = set()
    all_rows = query("SELECT DISTINCT substr(date,1,7) as m FROM sales ORDER BY m DESC")
    for r in all_rows:
        monthly.append(r["m"])

    return render_template("sales.html",
        rows=rows, total=total, month=month,
        monthly=monthly, today=today_jst(),
        msg=msg, error=error)

# ── シフト管理 ────────────────────────────────────────────────────

@app.route("/admin/shifts", methods=["GET", "POST"])
@admin_required
def admin_shifts():
    msg = error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name       = request.form.get("name", "").strip()
            shift_date = request.form.get("date", "").strip()
            start_time = request.form.get("start_time", "").strip()
            end_time   = request.form.get("end_time", "").strip()
            if not all([name, shift_date, start_time, end_time]):
                error = "全項目を入力してください"
            else:
                execute("INSERT INTO shifts (name, date, start_time, end_time) VALUES (?, ?, ?, ?)",
                        (name, shift_date, start_time, end_time))
                msg = "シフトを登録しました ✓"
        elif action == "delete":
            shift_id = request.form.get("shift_id")
            if shift_id:
                execute("DELETE FROM shifts WHERE id=?", (shift_id,))
                msg = "削除しました"

    # 表示週（月曜始まり）
    week_str = request.args.get("week", "")
    if week_str:
        try:
            week_start = date.fromisoformat(week_str)
        except ValueError:
            week_start = date.today()
    else:
        week_start = date.today()
    # 月曜に揃える
    week_start = week_start - timedelta(days=week_start.weekday())
    week_end   = week_start + timedelta(days=6)
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    rows = query(
        "SELECT * FROM shifts WHERE date BETWEEN ? AND ? ORDER BY date, start_time",
        (week_start.isoformat(), week_end.isoformat())
    )
    staff_rows = query("SELECT name FROM staff ORDER BY name")

    # 週別グリッド: {name: {date_str: [shifts]}}
    grid = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grid[r["name"]][r["date"]].append(r)

    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()

    return render_template("shifts.html",
        rows=rows, staff_rows=staff_rows,
        week_dates=week_dates, grid=grid,
        prev_week=prev_week, next_week=next_week,
        week_start=week_start,
        today_str=today_jst(),
        msg=msg, error=error)

# ── 商品・レシピ管理 ──────────────────────────────────────────────

@app.route("/admin/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    msg = error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name     = request.form.get("name", "").strip()
            category = request.form.get("category", "").strip()
            price    = request.form.get("price", "0").strip()
            cost     = request.form.get("cost", "0").strip()
            recipe   = request.form.get("recipe", "").strip()
            if not name:
                error = "商品名を入力してください"
            else:
                try:
                    execute("INSERT INTO products (name, category, price, cost, recipe) VALUES (?, ?, ?, ?, ?)",
                            (name, category, int(price or 0), int(cost or 0), recipe))
                    msg = f"「{name}」を登録しました ✓"
                except Exception:
                    error = f"「{name}」はすでに登録されています"
        elif action == "delete":
            prod_id = request.form.get("prod_id")
            if prod_id:
                execute("DELETE FROM products WHERE id=?", (prod_id,))
                msg = "削除しました"
        elif action == "edit":
            prod_id  = request.form.get("prod_id")
            name     = request.form.get("name", "").strip()
            category = request.form.get("category", "").strip()
            price    = request.form.get("price", "0").strip()
            cost     = request.form.get("cost", "0").strip()
            recipe   = request.form.get("recipe", "").strip()
            if prod_id and name:
                execute("UPDATE products SET name=?, category=?, price=?, cost=?, recipe=? WHERE id=?",
                        (name, category, int(price or 0), int(cost or 0), recipe, prod_id))
                msg = "更新しました ✓"

    category_filter = request.args.get("cat", "")
    if category_filter:
        products = query("SELECT * FROM products WHERE category=? ORDER BY category, name", (category_filter,))
    else:
        products = query("SELECT * FROM products ORDER BY category, name")

    categories = query("SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category")

    return render_template("products.html",
        products=products, categories=categories,
        category_filter=category_filter,
        msg=msg, error=error)

# ── ログイン ───────────────────────────────────────────────────────

@app.route("/admin/instagram", methods=["GET", "POST"])
@admin_required
def admin_instagram():
    results = []
    error = None
    if request.method == "POST":
        files = request.files.getlist("media")
        files = [f for f in files if f and f.filename]
        if not files:
            error = "ファイルを選択してください"
        else:
            try:
                import google.generativeai as genai
                import base64, mimetypes, json, re
                genai.configure(api_key=os.environ.get("GOOGLE_API_KEY", ""))
                model = genai.GenerativeModel("gemini-1.5-flash")

                RULES = (
                    "あなたはパン屋「ニチゲツ」のInstagram運用担当です。"
                    "投稿ルール: 製造風景40%・商品紹介30%・裏側20%・お知らせ10%。"
                    "キャプションは1〜3行、感情を書く、お客様目線。禁止: 長文・自慢・専門用語。"
                    "最重要: 「美味しそう」より「今日行かないと無くなりそう」を感じさせる。"
                    "キャプション末尾には必ず「プレオープン中のため売切れ次第終了となります。」を入れる。"
                )

                image_data_list = []
                for f in files:
                    raw = f.read()
                    mime = f.content_type or mimetypes.guess_type(f.filename)[0] or "image/jpeg"
                    b64 = base64.b64encode(raw).decode()
                    image_data_list.append({
                        "filename": f.filename,
                        "mime": mime,
                        "raw": raw,
                        "b64": b64,
                        "is_video": mime.startswith("video/"),
                    })

                for img in image_data_list:
                    if img["is_video"]:
                        prompt = (
                            f"{RULES}\n\n"
                            f"動画ファイル「{img['filename']}」についてInstagram Reelの編集提案をしてください。\n"
                            "以下のJSON形式のみで回答（```json不要）:\n"
                            '{"type":"製造風景", "caption":"キャプション文(改行は\\n)", "edit":"編集提案(カット順・テキスト・BGM)", "order_reason":"投稿順の理由"}'
                        )
                        resp = model.generate_content(prompt)
                    else:
                        prompt = (
                            f"{RULES}\n\n"
                            "この画像を見てInstagramキャプションと投稿タイプを提案してください。\n"
                            "typeは必ず「製造風景」「商品紹介」「裏側」「お知らせ」のいずれか。\n"
                            "以下のJSON形式のみで回答（```json不要）:\n"
                            '{"type":"商品紹介", "caption":"キャプション文(改行は\\n)", "edit":"投稿のポイント・加工提案", "order_reason":"投稿順の理由"}'
                        )
                        image_part = {"mime_type": img["mime"], "data": img["raw"]}
                        resp = model.generate_content([prompt, image_part])

                    raw_text = resp.text
                    m = re.search(r'\{.*\}', raw_text, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group())
                    else:
                        parsed = {"type":"不明","caption":raw_text,"edit":"","order_reason":""}

                    results.append({
                        "filename": img["filename"],
                        "is_video": img["is_video"],
                        "b64": img["b64"] if not img["is_video"] else "",
                        "mime": img["mime"],
                        **parsed
                    })

                # 投稿順を再整理（製造→商品→裏側→お知らせ）
                TYPE_ORDER = {"製造風景":0,"商品紹介":1,"裏側":2,"お知らせ":3}
                results.sort(key=lambda r: TYPE_ORDER.get(r.get("type","お知らせ"), 9))

            except Exception as e:
                error = f"分析エラー: {e}"

    return render_template("instagram.html", results=results, error=error)

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

try:
    init_db()
    print("DB初期化成功")
except Exception as e:
    print(f"DB初期化エラー: {e}")
    import traceback; traceback.print_exc()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
