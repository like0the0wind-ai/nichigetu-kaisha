import os
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort
)

JST = timezone(timedelta(hours=9))


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nichigetsu-shop-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "saito")

# 送料・支払い方法などの設定
SHOP_NAME = os.environ.get("SHOP_NAME", "パン屋ニチゲツ")

PAYMENT_METHODS = {
    "cod": "代金引換（商品お届け時にお支払い）",
    "bank": "銀行振込（前払い）",
}

# ── ヤマト クール便（冷凍）送料 ─────────────────────────────────────
# 商品ごとに箱サイズ（60/80/…）を持ち、お届け先の地域×サイズで送料を決める。
# 複数商品を一緒に買った場合は「一番大きいサイズ1つ分」の送料を請求する。

BOX_SIZES = [60, 80, 100, 120, 140, 160]  # 商品・送料テーブルで使う箱サイズ

# ヤマト運賃表の地帯区分（発送元から見た配送地域）
REGIONS = ["北海道", "北東北", "南東北", "関東", "信越", "北陸",
           "中部", "関西", "中国", "四国", "九州", "沖縄"]

# 都道府県 → 地帯（この順で都道府県プルダウンを作る）
PREF_REGION = {
    "北海道": "北海道",
    "青森県": "北東北", "秋田県": "北東北", "岩手県": "北東北",
    "宮城県": "南東北", "山形県": "南東北", "福島県": "南東北",
    "茨城県": "関東", "栃木県": "関東", "群馬県": "関東", "埼玉県": "関東",
    "千葉県": "関東", "東京都": "関東", "神奈川県": "関東", "山梨県": "関東",
    "新潟県": "信越", "長野県": "信越",
    "富山県": "北陸", "石川県": "北陸", "福井県": "北陸",
    "岐阜県": "中部", "静岡県": "中部", "愛知県": "中部", "三重県": "中部",
    "滋賀県": "関西", "京都府": "関西", "大阪府": "関西", "兵庫県": "関西",
    "奈良県": "関西", "和歌山県": "関西",
    "鳥取県": "中国", "島根県": "中国", "岡山県": "中国", "広島県": "中国", "山口県": "中国",
    "徳島県": "四国", "香川県": "四国", "愛媛県": "四国", "高知県": "四国",
    "福岡県": "九州", "佐賀県": "九州", "長崎県": "九州", "熊本県": "九州",
    "大分県": "九州", "宮崎県": "九州", "鹿児島県": "九州",
    "沖縄県": "沖縄",
}
PREFECTURES = list(PREF_REGION.keys())

# 送料テーブル初期値（関東発の目安・要確認）。0のセルは管理画面「送料設定」で入力。
# 発送元が確定したら管理画面で正規の金額に差し替えてください。
DEFAULT_RATES = {
    #            60    80    100   120   140   160
    "北海道":  [1830, 2130, 2440, 2750, 3060, 3370],
    "北東北":  [1390, 1690, 2000, 2310, 2620, 2930],
    "南東北":  [1280, 1580, 1890, 2200, 2510, 2820],
    "関東":    [1160, 1460, 1770, 2080, 2390, 2700],
    "信越":    [1160, 1460, 1770, 2080, 2390, 2700],
    "北陸":    [1280, 1580, 1890, 2200, 2510, 2820],
    "中部":    [1280, 1580, 1890, 2200, 2510, 2820],
    "関西":    [1390, 1690, 2000, 2310, 2620, 2930],
    "中国":    [1500, 1800, 2110, 2420, 2730, 3040],
    "四国":    [1500, 1800, 2110, 2420, 2730, 3040],
    "九州":    [1610, 1910, 2220, 2530, 2840, 3150],
    "沖縄":    [1490, 1970, 2450, 2930, 3410, 3890],
}

DATABASE_URL = os.environ.get("DATABASE_URL")


# ── DB ────────────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "shop.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn


def ph():
    return "%s" if DATABASE_URL else "?"


def query(sql, params=()):
    sql = sql.replace("?", ph())
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
    """INSERT/UPDATE/DELETE を実行し、必要なら採番されたidを返す"""
    sql = sql.replace("?", ph())
    conn = get_db()
    try:
        cur = conn.cursor()
        if DATABASE_URL and sql.strip().upper().startswith("INSERT"):
            cur.execute(sql + " RETURNING id", params)
            new_id = cur.fetchone()["id"]
            conn.commit()
            return new_id
        cur.execute(sql, params)
        conn.commit()
        return getattr(cur, "lastrowid", None)
    finally:
        conn.close()


def init_db():
    conn = get_db()
    try:
        cur = conn.cursor()
        serial = "SERIAL" if DATABASE_URL else "INTEGER"
        ai = "" if DATABASE_URL else "AUTOINCREMENT"

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS products (
                id          {serial} PRIMARY KEY {ai},
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                price       INTEGER NOT NULL DEFAULT 0,
                image_url   TEXT DEFAULT '',
                category    TEXT DEFAULT '',
                stock       INTEGER DEFAULT 0,
                size        INTEGER DEFAULT 60,
                sold_out    INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                sort_order  INTEGER DEFAULT 0
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS orders (
                id             {serial} PRIMARY KEY {ai},
                created_at     TEXT NOT NULL,
                customer_name  TEXT NOT NULL,
                email          TEXT DEFAULT '',
                phone          TEXT DEFAULT '',
                postal         TEXT DEFAULT '',
                prefecture     TEXT DEFAULT '',
                address        TEXT DEFAULT '',
                payment_method TEXT DEFAULT '',
                note           TEXT DEFAULT '',
                subtotal       INTEGER DEFAULT 0,
                shipping       INTEGER DEFAULT 0,
                box_size       INTEGER DEFAULT 0,
                total          INTEGER DEFAULT 0,
                status         TEXT DEFAULT '新規'
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS order_items (
                id         {serial} PRIMARY KEY {ai},
                order_id   INTEGER NOT NULL,
                product_id INTEGER,
                name       TEXT NOT NULL,
                price      INTEGER NOT NULL,
                qty        INTEGER NOT NULL,
                size       INTEGER DEFAULT 60
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS shipping_rates (
                id     {serial} PRIMARY KEY {ai},
                region TEXT NOT NULL,
                size   INTEGER NOT NULL,
                fee    INTEGER DEFAULT 0
            )
        """)
        conn.commit()

        # 既存DBに列が無い場合の追加（マイグレーション）
        for table, col, ddl in [
            ("products", "size", "INTEGER DEFAULT 60"),
            ("products", "sold_out", "INTEGER DEFAULT 0"),
            ("orders", "prefecture", "TEXT DEFAULT ''"),
            ("orders", "box_size", "INTEGER DEFAULT 0"),
            ("order_items", "size", "INTEGER DEFAULT 60"),
        ]:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                conn.commit()
            except Exception:
                conn.rollback()

        # 送料テーブルが空なら初期値を投入
        cur.execute("SELECT COUNT(*) AS c FROM shipping_rates")
        row = cur.fetchone()
        rcount = row["c"] if isinstance(row, dict) else row[0]
        if not rcount:
            for region, fees in DEFAULT_RATES.items():
                for size, fee in zip(BOX_SIZES, fees):
                    cur.execute(
                        ("INSERT INTO shipping_rates (region, size, fee) VALUES (%s,%s,%s)"
                         if DATABASE_URL else
                         "INSERT INTO shipping_rates (region, size, fee) VALUES (?,?,?)"),
                        (region, size, fee))
            conn.commit()

        # 商品が1件も無ければサンプルを投入
        cur.execute("SELECT COUNT(*) AS c FROM products")
        row = cur.fetchone()
        count = row["c"] if isinstance(row, dict) else row[0]
        if not count:
            samples = [
                ("ハンゲツセット", "人気のパンを少しずつ楽しめる、おひとり様やお試しにぴったりの詰め合わせ。",
                 3000, "🥐", "セット", 30, 60, 1),
                ("マンゲツセット", "定番から菓子パンまで詰め合わせた、ご家族で楽しめる満足のセット。",
                 5000, "🧺", "セット", 20, 80, 2),
                ("タイヨウセット", "当店自慢のパンを贅沢に集めた、ギフトにも喜ばれる最上級セット。",
                 8000, "🎁", "セット", 15, 120, 3),
            ]
            for name, desc, price, img, cat, stock, size, so in samples:
                cur.execute(
                    ("INSERT INTO products "
                     "(name, description, price, image_url, category, stock, size, is_active, sort_order) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s)"
                     if DATABASE_URL else
                     "INSERT INTO products "
                     "(name, description, price, image_url, category, stock, size, is_active, sort_order) "
                     "VALUES (?,?,?,?,?,?,?,1,?)"),
                    (name, desc, price, img, cat, stock, size, so),
                )
            conn.commit()
    finally:
        conn.close()


# ── カート（セッション） ───────────────────────────────────────────

def get_cart():
    """{ "product_id": qty } を返す"""
    return session.get("cart", {})


def save_cart(cart):
    session["cart"] = cart
    session.modified = True


def cart_detail():
    """カート内の商品を明細付きで返す"""
    cart = get_cart()
    items = []
    subtotal = 0
    for pid, qty in cart.items():
        rows = query("SELECT * FROM products WHERE id=?", (int(pid),))
        if not rows:
            continue
        p = dict(rows[0])
        line = p["price"] * qty
        subtotal += line
        items.append({"product": p, "qty": qty, "line_total": line})
    return items, subtotal


def cart_count():
    return sum(get_cart().values())


def cart_max_size(items):
    """カート内の商品の最大箱サイズ（表示用の目安）"""
    sizes = [int(it["product"].get("size") or 60) for it in items]
    return max(sizes) if sizes else 0


def cart_box_count(items):
    """発送する箱の数（商品ごと・数量分）"""
    return sum(it["qty"] for it in items)


def get_rate(region, size):
    rows = query("SELECT fee FROM shipping_rates WHERE region=? AND size=?",
                 (region, int(size)))
    return int(dict(rows[0])["fee"]) if rows else 0


def ship_by_region(items):
    """{地域: カート合計送料} を返す（商品ごとの箱サイズ送料 × 数量を合算）"""
    result = {}
    for region in REGIONS:
        total = 0
        for it in items:
            total += get_rate(region, int(it["product"].get("size") or 60)) * it["qty"]
        result[region] = total
    return result


def shipping_for_dest(items, prefecture):
    """お届け先都道府県とカート内容から送料を算出（商品ごとに加算）"""
    if not items or not prefecture:
        return 0
    region = PREF_REGION.get(prefecture)
    if not region:
        return 0
    return sum(get_rate(region, int(it["product"].get("size") or 60)) * it["qty"]
               for it in items)


@app.context_processor
def inject_globals():
    return {
        "cart_count": cart_count(),
        "shop_name": SHOP_NAME,
    }


# ── お客様側 ──────────────────────────────────────────────────────

@app.route("/")
def index():
    category = request.args.get("category", "").strip()
    if category:
        products = query(
            "SELECT * FROM products WHERE is_active=1 AND category=? "
            "ORDER BY sort_order, id", (category,))
    else:
        products = query(
            "SELECT * FROM products WHERE is_active=1 ORDER BY sort_order, id")
    cats = query(
        "SELECT DISTINCT category FROM products "
        "WHERE is_active=1 AND category != '' ORDER BY category")
    return render_template("index.html",
                           products=[dict(p) for p in products],
                           categories=[c["category"] for c in cats],
                           current_category=category)


@app.route("/product/<int:pid>")
def product_detail(pid):
    rows = query("SELECT * FROM products WHERE id=? AND is_active=1", (pid,))
    if not rows:
        abort(404)
    return render_template("product.html", product=dict(rows[0]))


@app.route("/cart")
def cart_view():
    items, subtotal = cart_detail()
    return render_template("cart.html", items=items,
                           subtotal=subtotal,
                           max_size=cart_max_size(items))


@app.route("/cart/add", methods=["POST"])
def cart_add():
    pid = request.form.get("product_id")
    qty = int(request.form.get("qty", "1") or 1)
    rows = query("SELECT * FROM products WHERE id=? AND is_active=1", (int(pid),))
    if not rows:
        abort(404)
    product = dict(rows[0])
    if product.get("sold_out"):
        flash(f"「{product['name']}」は現在売り切れです。", "warn")
        return redirect(request.form.get("next") or url_for("cart_view"))
    cart = get_cart()
    new_qty = cart.get(str(pid), 0) + qty
    # 在庫を超えないように
    if product["stock"] and new_qty > product["stock"]:
        new_qty = product["stock"]
        flash(f"「{product['name']}」は在庫上限（{product['stock']}個）までです。", "warn")
    cart[str(pid)] = max(1, new_qty)
    save_cart(cart)
    flash(f"「{product['name']}」をカートに入れました。", "ok")
    if request.form.get("buy_now"):
        return redirect(url_for("checkout"))
    return redirect(request.form.get("next") or url_for("cart_view"))


@app.route("/cart/update", methods=["POST"])
def cart_update():
    cart = get_cart()
    for key, val in request.form.items():
        if key.startswith("qty_"):
            pid = key[4:]
            try:
                q = int(val)
            except ValueError:
                q = 0
            if q <= 0:
                cart.pop(pid, None)
            else:
                cart[pid] = q
    save_cart(cart)
    return redirect(url_for("cart_view"))


@app.route("/cart/remove/<pid>", methods=["POST"])
def cart_remove(pid):
    cart = get_cart()
    cart.pop(str(pid), None)
    save_cart(cart)
    return redirect(url_for("cart_view"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    items, subtotal = cart_detail()
    if not items:
        return redirect(url_for("cart_view"))
    max_size = cart_max_size(items)

    def render(form, prefecture=""):
        shipping = shipping_for_dest(items, prefecture)
        return render_template(
            "checkout.html", items=items, subtotal=subtotal,
            max_size=max_size, box_count=cart_box_count(items), shipping=shipping,
            total=subtotal + shipping, payment_methods=PAYMENT_METHODS,
            prefectures=PREFECTURES, pref_region=PREF_REGION,
            ship_by_region=ship_by_region(items), form=form)

    if request.method == "POST":
        name = request.form.get("customer_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        postal = request.form.get("postal", "").strip()
        prefecture = request.form.get("prefecture", "").strip()
        address = request.form.get("address", "").strip()
        payment = request.form.get("payment_method", "").strip()
        note = request.form.get("note", "").strip()

        errors = []
        if not name:
            errors.append("お名前を入力してください。")
        if not phone:
            errors.append("電話番号を入力してください。")
        if prefecture not in PREF_REGION:
            errors.append("お届け先の都道府県を選択してください。")
        if not address:
            errors.append("お届け先住所（市区町村以降）を入力してください。")
        if payment not in PAYMENT_METHODS:
            errors.append("お支払い方法を選択してください。")

        if errors:
            for e in errors:
                flash(e, "warn")
            return render(request.form, prefecture)

        # 送料はサーバー側で正式に計算（改ざん防止）
        shipping = shipping_for_dest(items, prefecture)
        total = subtotal + shipping

        order_id = execute(
            "INSERT INTO orders (created_at, customer_name, email, phone, postal, "
            "prefecture, address, payment_method, note, subtotal, shipping, box_size, total, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now_jst(), name, email, phone, postal, prefecture, address,
             payment, note, subtotal, shipping, cart_box_count(items), total, "新規"))

        for it in items:
            p = it["product"]
            execute(
                "INSERT INTO order_items (order_id, product_id, name, price, qty, size) "
                "VALUES (?,?,?,?,?,?)",
                (order_id, p["id"], p["name"], p["price"], it["qty"],
                 int(p.get("size") or 60)))
            if p["stock"]:
                execute("UPDATE products SET stock = stock - ? WHERE id=?",
                        (it["qty"], p["id"]))

        save_cart({})
        return redirect(url_for("complete", order_id=order_id))

    return render({})


@app.route("/complete/<int:order_id>")
def complete(order_id):
    rows = query("SELECT * FROM orders WHERE id=?", (order_id,))
    if not rows:
        abort(404)
    order = dict(rows[0])
    items = [dict(r) for r in query(
        "SELECT * FROM order_items WHERE order_id=?", (order_id,))]
    return render_template("complete.html", order=order, items=items,
                           payment_methods=PAYMENT_METHODS,
                           pref_region=PREF_REGION)


# ── 管理側 ────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(request.args.get("next") or url_for("admin"))
        flash("パスワードが違います。", "warn")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin():
    status = request.args.get("status", "").strip()
    if status:
        orders = query(
            "SELECT * FROM orders WHERE status=? ORDER BY id DESC", (status,))
    else:
        orders = query("SELECT * FROM orders ORDER BY id DESC")
    new_count = query("SELECT COUNT(*) AS c FROM orders WHERE status=?", ("新規",))
    return render_template("admin_orders.html",
                           orders=[dict(o) for o in orders],
                           current_status=status,
                           new_count=new_count[0]["c"] if new_count else 0)


@app.route("/admin/order/<int:order_id>", methods=["GET", "POST"])
@admin_required
def admin_order(order_id):
    rows = query("SELECT * FROM orders WHERE id=?", (order_id,))
    if not rows:
        abort(404)
    if request.method == "POST":
        new_status = request.form.get("status", "").strip()
        if new_status:
            execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
        return redirect(url_for("admin_order", order_id=order_id))
    order = dict(rows[0])
    items = [dict(r) for r in query(
        "SELECT * FROM order_items WHERE order_id=?", (order_id,))]
    return render_template("admin_order.html", order=order, items=items,
                           payment_methods=PAYMENT_METHODS,
                           statuses=["新規", "対応中", "発送済み", "完了", "キャンセル"])


@app.route("/admin/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            execute(
                "INSERT INTO products (name, description, price, image_url, "
                "category, stock, size, sold_out, is_active, sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (request.form.get("name", "").strip(),
                 request.form.get("description", "").strip(),
                 int(request.form.get("price") or 0),
                 request.form.get("image_url", "").strip(),
                 request.form.get("category", "").strip(),
                 int(request.form.get("stock") or 0),
                 int(request.form.get("size") or 60),
                 1 if request.form.get("sold_out") else 0,
                 1 if request.form.get("is_active") else 0,
                 int(request.form.get("sort_order") or 0)))
            flash("商品を追加しました。", "ok")
        elif action == "update":
            pid = request.form.get("id")
            execute(
                "UPDATE products SET name=?, description=?, price=?, image_url=?, "
                "category=?, stock=?, size=?, sold_out=?, is_active=?, sort_order=? WHERE id=?",
                (request.form.get("name", "").strip(),
                 request.form.get("description", "").strip(),
                 int(request.form.get("price") or 0),
                 request.form.get("image_url", "").strip(),
                 request.form.get("category", "").strip(),
                 int(request.form.get("stock") or 0),
                 int(request.form.get("size") or 60),
                 1 if request.form.get("sold_out") else 0,
                 1 if request.form.get("is_active") else 0,
                 int(request.form.get("sort_order") or 0),
                 int(pid)))
            flash("商品を更新しました。", "ok")
        elif action == "delete":
            execute("DELETE FROM products WHERE id=?", (int(request.form.get("id")),))
            flash("商品を削除しました。", "ok")
        return redirect(url_for("admin_products"))

    products = [dict(p) for p in query(
        "SELECT * FROM products ORDER BY sort_order, id")]
    return render_template("admin_products.html", products=products,
                           box_sizes=BOX_SIZES)


@app.route("/admin/shipping", methods=["GET", "POST"])
@admin_required
def admin_shipping():
    if request.method == "POST":
        for region in REGIONS:
            for size in BOX_SIZES:
                key = f"fee_{region}_{size}"
                if key in request.form:
                    fee = int(request.form.get(key) or 0)
                    rows = query(
                        "SELECT id FROM shipping_rates WHERE region=? AND size=?",
                        (region, size))
                    if rows:
                        execute("UPDATE shipping_rates SET fee=? WHERE region=? AND size=?",
                                (fee, region, size))
                    else:
                        execute("INSERT INTO shipping_rates (region, size, fee) VALUES (?,?,?)",
                                (region, size, fee))
        flash("送料設定を保存しました。", "ok")
        return redirect(url_for("admin_shipping"))

    # {region: {size: fee}}
    grid = {r: {s: 0 for s in BOX_SIZES} for r in REGIONS}
    for row in query("SELECT region, size, fee FROM shipping_rates"):
        d = dict(row)
        if d["region"] in grid and d["size"] in grid[d["region"]]:
            grid[d["region"]][d["size"]] = int(d["fee"])
    # 使用中サイズ（商品が使っている箱サイズ）を強調
    used = query("SELECT DISTINCT size FROM products WHERE is_active=1")
    used_sizes = sorted({int(dict(r)["size"]) for r in used})
    return render_template("admin_shipping.html", grid=grid, regions=REGIONS,
                           box_sizes=BOX_SIZES, pref_region=PREF_REGION,
                           used_sizes=used_sizes)


# アプリ起動時にテーブルを用意
with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5002)))
