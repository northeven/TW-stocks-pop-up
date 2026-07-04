"""後台管理(Blueprint):商品/分類/取貨點/設定/訂單/匯入/報表。"""
import csv
import io
import os
import secrets
import time
from functools import wraps

from flask import (Blueprint, Response, abort, flash, g, redirect,
                   render_template, request, session, url_for)

import config
import lineapi
import store
from db import get_db, log_status, now_tw, set_setting, today_tw
from security import hash_password, verify_password

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# 簡易登入防爆破:IP -> (失敗次數, 最後失敗時間)
_login_failures = {}
MAX_FAILURES, LOCK_SECONDS = 8, 300


def ensure_admin_user():
    """第一次啟動時建立 admin 帳號(密碼雜湊儲存)。由 app.py 在 init_db() 後呼叫。"""
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) c FROM admin_user").fetchone()
        if row["c"] == 0:
            using_default = "ADMIN_INIT_PASSWORD" not in os.environ
            conn.execute(
                "INSERT INTO admin_user(username, password_hash, must_change, created_at) "
                "VALUES ('admin', ?, ?, ?)",
                (hash_password(config.ADMIN_INIT_PASSWORD), 1 if using_default else 0, now_tw()),
            )
            conn.commit()
    finally:
        conn.close()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin.login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- 登入

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0]
        fails, last = _login_failures.get(ip, (0, 0))
        if fails >= MAX_FAILURES and time.time() - last < LOCK_SECONDS:
            flash("嘗試次數過多,請 5 分鐘後再試")
            return render_template("admin/login.html")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = g.db.execute(
            "SELECT * FROM admin_user WHERE username = ?", (username,)
        ).fetchone()
        if user and verify_password(password, user["password_hash"]):
            _login_failures.pop(ip, None)
            session["admin_id"] = user["id"]
            session["admin_name"] = user["username"]
            session["must_change_pw"] = bool(user["must_change"])
            return redirect(request.args.get("next") or url_for("admin.dashboard"))
        _login_failures[ip] = (fails + 1, time.time())
        flash("帳號或密碼錯誤")
    return render_template("admin/login.html")


@admin_bp.route("/logout", methods=["POST"])
def logout():
    session.pop("admin_id", None)
    session.pop("admin_name", None)
    return redirect(url_for("admin.login"))


@admin_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        user = g.db.execute(
            "SELECT * FROM admin_user WHERE id = ?", (session["admin_id"],)
        ).fetchone()
        if not verify_password(old, user["password_hash"]):
            flash("目前密碼錯誤")
        elif len(new) < 8:
            flash("新密碼至少需要 8 個字元")
        elif new != confirm:
            flash("兩次輸入的新密碼不一致")
        else:
            g.db.execute(
                "UPDATE admin_user SET password_hash = ?, must_change = 0 WHERE id = ?",
                (hash_password(new), user["id"]),
            )
            g.db.commit()
            session["must_change_pw"] = False
            flash("密碼已更新")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/password.html")


# ---------------------------------------------------------------- 儀表板

@admin_bp.route("/")
@login_required
def dashboard():
    db = g.db
    today = today_tw()
    month = today[:7]
    stats = {
        "today_orders": db.execute(
            "SELECT COUNT(*) c FROM orders WHERE created_at LIKE ?", (today + "%",)
        ).fetchone()["c"],
        "pending": db.execute(
            "SELECT COUNT(*) c FROM orders WHERE order_status = 'pending'"
        ).fetchone()["c"],
        "pending_check": db.execute(
            "SELECT COUNT(*) c FROM orders WHERE payment_status = 'pending_check'"
        ).fetchone()["c"],
        "ready": db.execute(
            "SELECT COUNT(*) c FROM orders WHERE order_status = 'ready'"
        ).fetchone()["c"],
        "month_revenue": db.execute(
            "SELECT COALESCE(SUM(total_amount),0) s FROM orders "
            "WHERE created_at LIKE ? AND order_status != 'cancelled'", (month + "%",)
        ).fetchone()["s"],
        "month_paid": db.execute(
            "SELECT COALESCE(SUM(total_amount),0) s FROM orders "
            "WHERE created_at LIKE ? AND payment_status = 'paid'", (month + "%",)
        ).fetchone()["s"],
    }
    low_stock = db.execute(
        "SELECT * FROM product WHERE is_active = 1 AND stock_qty IS NOT NULL "
        "AND stock_qty <= 5 ORDER BY stock_qty LIMIT 10"
    ).fetchall()
    no_price = db.execute(
        "SELECT COUNT(*) c FROM product WHERE price <= 0 AND is_active = 1"
    ).fetchone()["c"]
    recent = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 10").fetchall()
    top_products = db.execute(
        "SELECT product_name, SUM(qty) total_qty, SUM(subtotal) total_amt "
        "FROM order_item oi JOIN orders o ON o.id = oi.order_id "
        "WHERE o.order_status != 'cancelled' AND o.created_at LIKE ? "
        "GROUP BY product_name ORDER BY total_qty DESC LIMIT 5", (month + "%",)
    ).fetchall()
    return render_template("admin/dashboard.html", stats=stats, low_stock=low_stock,
                           recent=recent, no_price=no_price, top_products=top_products)


# ---------------------------------------------------------------- 商品

@admin_bp.route("/products")
@login_required
def products():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    filt = request.args.get("filter", "")
    sql = ("SELECT p.*, c.name category_name FROM product p "
           "LEFT JOIN category c ON c.id = p.category_id WHERE 1=1")
    args = []
    if q:
        sql += " AND (p.name LIKE ? OR p.tag LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if cat:
        sql += " AND p.category_id = ?"
        args.append(cat)
    if filt == "no_price":
        sql += " AND p.price <= 0"
    elif filt == "inactive":
        sql += " AND p.is_active = 0"
    elif filt == "low_stock":
        sql += " AND p.stock_qty IS NOT NULL AND p.stock_qty <= 5"
    sql += " ORDER BY c.sort_order, p.sort_order, p.id"
    rows = g.db.execute(sql, args).fetchall()
    cats = g.db.execute("SELECT * FROM category ORDER BY sort_order, id").fetchall()
    return render_template("admin/products.html", products=rows, categories=cats,
                           q=q, cat=cat, filt=filt)


def _save_image(file_storage, crop=True):
    """驗證並儲存上傳圖片,回傳檔名。有 Pillow 時裁切成正方形封面。"""
    filename = file_storage.filename or ""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in config.ALLOWED_IMAGE_EXT:
        raise ValueError(f"不支援的圖片格式:{filename}(限 jpg/png/webp/gif)")
    token = secrets.token_hex(12)
    if HAS_PIL and ext != ".gif":
        img = Image.open(file_storage.stream)
        img = img.convert("RGB")
        if crop:
            w, h = img.size
            side = min(w, h)
            img = img.crop(((w - side) // 2, (h - side) // 2,
                            (w + side) // 2, (h + side) // 2))
            img = img.resize(config.COVER_SIZE, Image.LANCZOS)
        else:
            img.thumbnail((1200, 1200), Image.LANCZOS)
        out = config.UPLOAD_DIR / f"{token}.jpg"
        img.save(out, "JPEG", quality=85)
        return out.name
    out = config.UPLOAD_DIR / f"{token}{ext}"
    file_storage.save(out)
    return out.name


def _product_form_save(pid=None):
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        flash("商品名稱必填")
        return None
    try:
        price = int(f.get("price") or 0)
    except ValueError:
        price = 0
    original_price = f.get("original_price") or None
    stock = f.get("stock_qty", "").strip()
    stock_qty = int(stock) if stock.lstrip("-").isdigit() else None
    values = {
        "category_id": f.get("category_id") or None,
        "name": name,
        "spec": f.get("spec", "").strip(),
        "description": f.get("description", "").strip(),
        "price": price,
        "original_price": int(original_price) if original_price else None,
        "stock_qty": stock_qty,
        "tag": f.get("tag", "").strip(),
        "is_limited": 1 if f.get("is_limited") else 0,
        "is_active": 1 if f.get("is_active") else 0,
        "sort_order": int(f.get("sort_order") or 0),
        "sell_start": f.get("sell_start", "").strip(),
        "sell_end": f.get("sell_end", "").strip(),
    }
    db = g.db
    if pid:
        sets = ", ".join(f"{k} = ?" for k in values)
        db.execute(f"UPDATE product SET {sets} WHERE id = ?", [*values.values(), pid])
    else:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = db.execute(
            f"INSERT INTO product({cols}, created_at) VALUES ({marks}, ?)",
            [*values.values(), now_tw()],
        )
        pid = cur.lastrowid

    # 圖片上傳:第一張(或勾選)為封面,其餘進相簿
    files = [fs for fs in request.files.getlist("images") if fs and fs.filename]
    for i, fs in enumerate(files):
        try:
            product = db.execute("SELECT image_url FROM product WHERE id = ?", (pid,)).fetchone()
            if i == 0 and not product["image_url"]:
                fname = _save_image(fs, crop=True)
                db.execute("UPDATE product SET image_url = ? WHERE id = ?", (fname, pid))
            else:
                fname = _save_image(fs, crop=False)
                db.execute(
                    "INSERT INTO product_image(product_id, filename, sort_order) VALUES (?, ?, ?)",
                    (pid, fname, i),
                )
        except ValueError as e:
            flash(str(e))
    db.commit()
    return pid


@admin_bp.route("/products/new", methods=["GET", "POST"])
@login_required
def product_new():
    if request.method == "POST":
        pid = _product_form_save()
        if pid:
            flash("商品已建立")
            return redirect(url_for("admin.product_edit", pid=pid))
    cats = g.db.execute("SELECT * FROM category ORDER BY sort_order, id").fetchall()
    return render_template("admin/product_form.html", p=None, images=[], categories=cats)


@admin_bp.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def product_edit(pid):
    if request.method == "POST":
        if _product_form_save(pid):
            flash("已儲存")
            return redirect(url_for("admin.product_edit", pid=pid))
    p = g.db.execute("SELECT * FROM product WHERE id = ?", (pid,)).fetchone()
    if not p:
        abort(404)
    images = g.db.execute(
        "SELECT * FROM product_image WHERE product_id = ? ORDER BY sort_order, id", (pid,)
    ).fetchall()
    cats = g.db.execute("SELECT * FROM category ORDER BY sort_order, id").fetchall()
    return render_template("admin/product_form.html", p=p, images=images, categories=cats)


@admin_bp.route("/products/<int:pid>/toggle", methods=["POST"])
@login_required
def product_toggle(pid):
    g.db.execute("UPDATE product SET is_active = 1 - is_active WHERE id = ?", (pid,))
    g.db.commit()
    return redirect(request.referrer or url_for("admin.products"))


@admin_bp.route("/products/<int:pid>/delete", methods=["POST"])
@login_required
def product_delete(pid):
    used = g.db.execute(
        "SELECT COUNT(*) c FROM order_item WHERE product_id = ?", (pid,)
    ).fetchone()["c"]
    if used:
        # 有訂單引用過的商品改成下架,保留歷史紀錄
        g.db.execute("UPDATE product SET is_active = 0 WHERE id = ?", (pid,))
        flash("此商品已有訂單紀錄,已改為下架(保留報表資料)")
    else:
        g.db.execute("DELETE FROM product_image WHERE product_id = ?", (pid,))
        g.db.execute("DELETE FROM product WHERE id = ?", (pid,))
        flash("商品已刪除")
    g.db.commit()
    return redirect(url_for("admin.products"))


@admin_bp.route("/products/<int:pid>/image/<int:img_id>/delete", methods=["POST"])
@login_required
def product_image_delete(pid, img_id):
    g.db.execute("DELETE FROM product_image WHERE id = ? AND product_id = ?", (img_id, pid))
    g.db.commit()
    return redirect(url_for("admin.product_edit", pid=pid))


@admin_bp.route("/products/<int:pid>/cover/clear", methods=["POST"])
@login_required
def product_cover_clear(pid):
    g.db.execute("UPDATE product SET image_url = '' WHERE id = ?", (pid,))
    g.db.commit()
    return redirect(url_for("admin.product_edit", pid=pid))


# ---------------------------------------------------------------- 分類/取貨點

@admin_bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    db = g.db
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = request.form.get("name", "").strip()
            if name:
                try:
                    db.execute(
                        "INSERT INTO category(name, sort_order) VALUES (?, "
                        "(SELECT COALESCE(MAX(sort_order),0)+1 FROM category))", (name,))
                    db.commit()
                except Exception:
                    flash("分類名稱重複")
        elif action == "update":
            cid = request.form.get("id")
            db.execute(
                "UPDATE category SET name = ?, sort_order = ?, is_active = ? WHERE id = ?",
                (request.form.get("name", "").strip(),
                 int(request.form.get("sort_order") or 0),
                 1 if request.form.get("is_active") else 0, cid))
            db.commit()
        elif action == "delete":
            cid = request.form.get("id")
            used = db.execute(
                "SELECT COUNT(*) c FROM product WHERE category_id = ?", (cid,)
            ).fetchone()["c"]
            if used:
                flash(f"此分類下還有 {used} 個商品,請先移動或刪除商品")
            else:
                db.execute("DELETE FROM category WHERE id = ?", (cid,))
                db.commit()
        return redirect(url_for("admin.categories"))
    rows = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM product p WHERE p.category_id = c.id) cnt "
        "FROM category c ORDER BY c.sort_order, c.id"
    ).fetchall()
    return render_template("admin/categories.html", categories=rows)


@admin_bp.route("/pickup-points", methods=["GET", "POST"])
@login_required
def pickup_points():
    db = g.db
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = request.form.get("name", "").strip()
            if name:
                db.execute(
                    "INSERT INTO pickup_point(name, address, note, sort_order) VALUES (?, ?, ?, "
                    "(SELECT COALESCE(MAX(sort_order),0)+1 FROM pickup_point))",
                    (name, request.form.get("address", "").strip(),
                     request.form.get("note", "").strip()))
                db.commit()
        elif action == "update":
            db.execute(
                "UPDATE pickup_point SET name=?, address=?, note=?, sort_order=?, is_active=? "
                "WHERE id = ?",
                (request.form.get("name", "").strip(),
                 request.form.get("address", "").strip(),
                 request.form.get("note", "").strip(),
                 int(request.form.get("sort_order") or 0),
                 1 if request.form.get("is_active") else 0,
                 request.form.get("id")))
            db.commit()
        elif action == "delete":
            ppid = request.form.get("id")
            used = db.execute(
                "SELECT COUNT(*) c FROM orders WHERE pickup_point_id = ?", (ppid,)
            ).fetchone()["c"]
            if used:
                db.execute("UPDATE pickup_point SET is_active = 0 WHERE id = ?", (ppid,))
                flash("此取貨點已有訂單紀錄,已改為停用")
            else:
                db.execute("DELETE FROM pickup_point WHERE id = ?", (ppid,))
            db.commit()
        return redirect(url_for("admin.pickup_points"))
    rows = db.execute("SELECT * FROM pickup_point ORDER BY sort_order, id").fetchall()
    return render_template("admin/pickup_points.html", points=rows)


# ---------------------------------------------------------------- 系統設定

SETTING_FIELDS = [
    ("shop_name", "商店名稱"),
    ("shop_intro", "商店簡介(顯示在前台頁首)"),
    ("announcement", "公告(顯示在前台最上方,留空則不顯示)"),
    ("min_order_amount", "最低訂購金額(NT$)"),
    ("preorder_days", "預購到貨天數(例:3-5)"),
    ("pickup_deadline_days", "到貨後取貨期限(天)"),
    ("order_prefix", "訂單編號前綴"),
    ("line_url", "LINE 官方帳號加好友連結(例:https://lin.ee/xxxx)"),
    ("bank_info", "轉帳資訊(銀行/帳號/戶名,會顯示給選轉帳的客人)"),
]


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        for key, _ in SETTING_FIELDS:
            if key in request.form:
                set_setting(g.db, key, request.form.get(key, "").strip())
        set_setting(g.db, "enable_cod", "1" if request.form.get("enable_cod") else "0")
        set_setting(g.db, "enable_transfer", "1" if request.form.get("enable_transfer") else "0")
        g.db.commit()
        flash("設定已儲存")
        return redirect(url_for("admin.settings_page"))
    return render_template("admin/settings.html", fields=SETTING_FIELDS)


# ---------------------------------------------------------------- 訂單

def _notify_ready(order):
    """到貨通知(需客人曾在 LINE 查詢過訂單完成綁定)。"""
    if not (config.line_enabled() and order["line_user_id"]):
        return False
    deadline = g.settings.get("pickup_deadline_days", "3")
    return lineapi.push_text(
        order["line_user_id"],
        f"📦 到貨通知\n您的訂單 {order['order_no']} 已送達「{order['pickup_point_name']}」!\n"
        f"請於 {deadline} 天內取貨,謝謝 🙏",
    )


def _apply_status(db, order, new_status, operator):
    old = order["order_status"]
    if new_status == old:
        return
    if new_status == "cancelled" and old != "cancelled":
        store.restock_order(db, order["id"])
    if old == "cancelled" and new_status != "cancelled":
        # 從取消復原:重新扣庫存(不足就直接扣到負值提醒人工處理)
        for it in db.execute("SELECT * FROM order_item WHERE order_id = ?", (order["id"],)):
            if it["product_id"]:
                db.execute(
                    "UPDATE product SET stock_qty = CASE WHEN stock_qty IS NULL THEN NULL "
                    "ELSE stock_qty - ? END, sold_qty = sold_qty + ? WHERE id = ?",
                    (it["qty"], it["qty"], it["product_id"]))
    extra = ""
    if new_status == "ready":
        db.execute("UPDATE orders SET arrived_at = ? WHERE id = ?", (now_tw(), order["id"]))
        if _notify_ready(order):
            extra = "(已發送 LINE 到貨通知)"
    db.execute(
        "UPDATE orders SET order_status = ?, updated_at = ? WHERE id = ?",
        (new_status, now_tw(), order["id"]))
    log_status(db, order["id"], "order_status", old, new_status, operator)
    return extra


@admin_bp.route("/orders")
@login_required
def orders():
    db = g.db
    status = request.args.get("status", "")
    pay = request.args.get("pay", "")
    pickup = request.args.get("pickup", "")
    q = request.args.get("q", "").strip()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    sql = "SELECT * FROM orders WHERE 1=1"
    args = []
    if status:
        sql += " AND order_status = ?"
        args.append(status)
    if pay:
        sql += " AND payment_status = ?"
        args.append(pay)
    if pickup:
        sql += " AND pickup_point_id = ?"
        args.append(pickup)
    if q:
        sql += " AND (order_no LIKE ? OR customer_name LIKE ? OR phone LIKE ?)"
        args += [f"%{q}%"] * 3
    if date_from:
        sql += " AND created_at >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND created_at <= ?"
        args.append(date_to + " 23:59:59")
    sql += " ORDER BY id DESC LIMIT 500"
    rows = db.execute(sql, args).fetchall()
    points = db.execute("SELECT * FROM pickup_point ORDER BY sort_order, id").fetchall()
    counts = {r["order_status"]: r["c"] for r in db.execute(
        "SELECT order_status, COUNT(*) c FROM orders GROUP BY order_status")}
    return render_template("admin/orders.html", orders=rows, points=points, counts=counts,
                           status=status, pay=pay, pickup=pickup, q=q,
                           date_from=date_from, date_to=date_to)


@admin_bp.route("/orders/bulk", methods=["POST"])
@login_required
def orders_bulk():
    ids = request.form.getlist("order_ids")
    new_status = request.form.get("new_status", "")
    if not ids or new_status not in store.ORDER_STATUS:
        flash("請勾選訂單並選擇要變更的狀態")
        return redirect(request.referrer or url_for("admin.orders"))
    notified = 0
    for oid in ids:
        order = g.db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
        if order:
            extra = _apply_status(g.db, order, new_status, session.get("admin_name", "admin"))
            if extra:
                notified += 1
    g.db.commit()
    msg = f"已將 {len(ids)} 筆訂單更新為「{store.ORDER_STATUS[new_status]}」"
    if notified:
        msg += f",其中 {notified} 筆已發送 LINE 到貨通知"
    flash(msg)
    return redirect(request.referrer or url_for("admin.orders"))


@admin_bp.route("/orders/<int:oid>", methods=["GET", "POST"])
@login_required
def order_detail(oid):
    db = g.db
    order = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not order:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        operator = session.get("admin_name", "admin")
        if action == "set_status":
            new_status = request.form.get("order_status", "")
            if new_status in store.ORDER_STATUS:
                extra = _apply_status(db, order, new_status, operator) or ""
                db.commit()
                flash(f"訂單狀態已更新為「{store.ORDER_STATUS[new_status]}」{extra}")
        elif action == "set_payment":
            new_pay = request.form.get("payment_status", "")
            if new_pay in store.PAYMENT_STATUS:
                log_status(db, oid, "payment_status", order["payment_status"], new_pay, operator)
                db.execute(
                    "UPDATE orders SET payment_status = ?, updated_at = ? WHERE id = ?",
                    (new_pay, now_tw(), oid))
                if new_pay == "paid":
                    db.execute(
                        """INSERT INTO payment_record(order_id, method, amount, status, created_at)
                           VALUES (?, ?, ?, 'paid(manual)', ?)""",
                        (oid, order["payment_method"], order["total_amount"], now_tw()))
                db.commit()
                flash("付款狀態已更新")
        elif action == "save_note":
            db.execute("UPDATE orders SET admin_note = ?, updated_at = ? WHERE id = ?",
                       (request.form.get("admin_note", "").strip(), now_tw(), oid))
            db.commit()
            flash("備註已儲存")
        elif action == "notify_ready":
            if _notify_ready(order):
                flash("已發送 LINE 到貨通知")
            else:
                flash("無法發送:客人尚未在 LINE 綁定此訂單,或 LINE 尚未設定")
        return redirect(url_for("admin.order_detail", oid=oid))
    items = db.execute("SELECT * FROM order_item WHERE order_id = ?", (oid,)).fetchall()
    payments = db.execute(
        "SELECT * FROM payment_record WHERE order_id = ? ORDER BY id DESC", (oid,)).fetchall()
    logs = db.execute(
        "SELECT * FROM status_log WHERE order_id = ? ORDER BY id DESC LIMIT 30", (oid,)).fetchall()
    return render_template("admin/order_detail.html", order=order, items=items,
                           payments=payments, logs=logs)


# ---------------------------------------------------------------- CSV 匯入

IMPORT_HEADERS = ["分類", "商品名稱", "規格", "售價", "原價", "庫存", "標籤", "商品說明", "上架"]


@admin_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_csv():
    result = None
    if request.method == "POST":
        fs = request.files.get("file")
        if not fs or not fs.filename:
            flash("請選擇 CSV 檔案")
        else:
            raw = fs.read()
            text = None
            for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                flash("無法辨識檔案編碼,請存成 UTF-8 或 Big5 CSV")
            else:
                result = _do_import(text)
    return render_template("admin/import.html", result=result, headers=IMPORT_HEADERS)


def _do_import(text):
    db = g.db
    reader = csv.DictReader(io.StringIO(text))
    ok, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):
        row = { (k or "").strip(): (v or "").strip() for k, v in row.items() }
        name = row.get("商品名稱", "")
        if not name:
            skipped += 1
            errors.append(f"第 {i} 列:缺少商品名稱,已略過")
            continue
        cat_name = row.get("分類", "") or "未分類"
        cat = db.execute("SELECT id FROM category WHERE name = ?", (cat_name,)).fetchone()
        if not cat:
            db.execute(
                "INSERT INTO category(name, sort_order) VALUES (?, "
                "(SELECT COALESCE(MAX(sort_order),0)+1 FROM category))", (cat_name,))
            cat = db.execute("SELECT id FROM category WHERE name = ?", (cat_name,)).fetchone()
        try:
            price = int(float(row.get("售價", "0") or 0))
        except ValueError:
            price = 0
        tag = row.get("標籤", "")
        if price <= 0 and "待補價格" not in tag:
            tag = (tag + " 待補價格").strip()
        original = row.get("原價", "")
        stock = row.get("庫存", "")
        db.execute(
            """INSERT INTO product(category_id, name, spec, price, original_price, stock_qty,
               tag, description, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cat["id"], name, row.get("規格", ""), price,
             int(float(original)) if original.replace(".", "").isdigit() else None,
             int(float(stock)) if stock.replace(".", "").isdigit() else None,
             tag, row.get("商品說明", ""),
             0 if row.get("上架", "") in ("0", "否", "N", "n") else 1, now_tw()))
        ok += 1
    db.commit()
    return {"ok": ok, "skipped": skipped, "errors": errors}


@admin_bp.route("/import/template.csv")
@login_required
def import_template():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(IMPORT_HEADERS)
    writer.writerow(["人氣麵包", "日式生吐司", "1 條(約450g)", "120", "150", "30", "人氣", "口感綿密", "1"])
    writer.writerow(["冷凍即食", "舒肥雞胸肉", "180g×2/包", "130", "", "", "", "", "1"])
    data = "﻿" + out.getvalue()  # BOM 讓 Excel 正確顯示中文
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=products_template.csv"})


# ---------------------------------------------------------------- 對帳報表

@admin_bp.route("/report")
@login_required
def report():
    date_from = request.args.get("from", today_tw()[:8] + "01")
    date_to = request.args.get("to", today_tw())
    rows = g.db.execute(
        """SELECT o.*, (SELECT GROUP_CONCAT(product_name || ' x' || qty, ' / ')
           FROM order_item WHERE order_id = o.id) items_text
           FROM orders o WHERE o.created_at >= ? AND o.created_at <= ?
           ORDER BY o.id""", (date_from, date_to + " 23:59:59")).fetchall()
    summary = {
        "count": len([r for r in rows if r["order_status"] != "cancelled"]),
        "total": sum(r["total_amount"] for r in rows if r["order_status"] != "cancelled"),
        "paid": sum(r["total_amount"] for r in rows if r["payment_status"] == "paid"),
        "unpaid": sum(r["total_amount"] for r in rows
                      if r["payment_status"] in ("unpaid", "pending_check")
                      and r["order_status"] != "cancelled"),
    }
    by_pickup = {}
    by_method = {}
    for r in rows:
        if r["order_status"] == "cancelled":
            continue
        by_pickup.setdefault(r["pickup_point_name"] or "?", [0, 0])
        by_pickup[r["pickup_point_name"] or "?"][0] += 1
        by_pickup[r["pickup_point_name"] or "?"][1] += r["total_amount"]
        m = store.PAYMENT_METHOD.get(r["payment_method"], r["payment_method"])
        by_method.setdefault(m, [0, 0])
        by_method[m][0] += 1
        by_method[m][1] += r["total_amount"]
    return render_template("admin/report.html", rows=rows, summary=summary,
                           by_pickup=by_pickup, by_method=by_method,
                           date_from=date_from, date_to=date_to)


@admin_bp.route("/report.csv")
@login_required
def report_csv():
    date_from = request.args.get("from", "2000-01-01")
    date_to = request.args.get("to", "2099-12-31")
    rows = g.db.execute(
        """SELECT o.*, (SELECT GROUP_CONCAT(product_name || ' x' || qty, ' / ')
           FROM order_item WHERE order_id = o.id) items_text
           FROM orders o WHERE o.created_at >= ? AND o.created_at <= ?
           ORDER BY o.id""", (date_from, date_to + " 23:59:59")).fetchall()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["訂單編號", "下單時間", "姓名", "電話", "取貨點", "商品明細",
                     "金額", "付款方式", "付款狀態", "轉帳末五碼", "訂單狀態", "備註"])
    for r in rows:
        writer.writerow([
            r["order_no"], r["created_at"], r["customer_name"], r["phone"],
            r["pickup_point_name"], r["items_text"] or "", r["total_amount"],
            store.PAYMENT_METHOD.get(r["payment_method"], r["payment_method"]),
            store.PAYMENT_STATUS.get(r["payment_status"], r["payment_status"]),
            r["transfer_last5"],
            store.ORDER_STATUS.get(r["order_status"], r["order_status"]),
            r["note"]])
    data = "﻿" + out.getvalue()
    return Response(data, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=orders_{date_from}_{date_to}.csv"})
