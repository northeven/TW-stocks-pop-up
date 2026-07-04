"""Vitaslow Freshbox™ 團購結帳系統 — 主程式(前台 + Webhook)。

執行:python3 app.py(開發)/ gunicorn app:app(正式)
後台:/admin(見 admin.py)
"""
import json
import logging
import re
import secrets

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)

import config
import ecpay
import lineapi
import store
from db import get_db, get_settings, init_db, now_tw

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("freshbox")

init_db()

from admin import admin_bp, ensure_admin_user  # noqa: E402  (需要先 init_db)

ensure_admin_user()

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.register_blueprint(admin_bp)


# ---------------------------------------------------------------- 共用

@app.before_request
def before_request():
    g.db = get_db()
    g.settings = get_settings(g.db)
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    # CSRF:所有 POST 表單都要帶 token(webhook 走簽章驗證,豁免)
    if request.method == "POST" and not request.path.startswith("/webhook/"):
        token = request.form.get("csrf_token", "")
        if not token or token != session.get("csrf_token"):
            abort(400, "CSRF 驗證失敗,請重新整理頁面後再試")


@app.teardown_request
def teardown_request(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    return {
        "settings": g.get("settings", {}),
        "cart_count": sum(int(q) for q in cart.values()) if cart else 0,
        "csrf_token": session.get("csrf_token", ""),
        "ORDER_STATUS": store.ORDER_STATUS,
        "PAYMENT_STATUS": store.PAYMENT_STATUS,
        "PAYMENT_METHOD": store.PAYMENT_METHOD,
        "STATUS_FLOW": store.STATUS_FLOW,
        "ecpay_enabled": ecpay.enabled(),
        "line_enabled": config.line_enabled(),
    }


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(config.UPLOAD_DIR, filename)


def base_url() -> str:
    return config.PUBLIC_BASE_URL or request.url_root.rstrip("/")


# ---------------------------------------------------------------- 前台

@app.route("/")
def index():
    cats = g.db.execute(
        """SELECT c.* FROM category c
           WHERE c.is_active = 1 AND EXISTS (
             SELECT 1 FROM product p WHERE p.category_id = c.id AND p.is_active = 1)
           ORDER BY c.sort_order, c.id"""
    ).fetchall()
    products_by_cat = {}
    for c in cats:
        rows = g.db.execute(
            "SELECT * FROM product WHERE category_id = ? AND is_active = 1 "
            "ORDER BY sort_order, id", (c["id"],)
        ).fetchall()
        rows = [p for p in rows if store.product_on_sale(p)]
        if rows:
            products_by_cat[c["id"]] = rows
    cats = [c for c in cats if c["id"] in products_by_cat]
    return render_template("shop/index.html", categories=cats,
                           products_by_cat=products_by_cat)


@app.route("/product/<int:pid>")
def product_detail(pid):
    p = g.db.execute("SELECT * FROM product WHERE id = ? AND is_active = 1", (pid,)).fetchone()
    if not p or not store.product_on_sale(p):
        flash("此商品目前未販售")
        return redirect(url_for("index"))
    images = g.db.execute(
        "SELECT * FROM product_image WHERE product_id = ? ORDER BY sort_order, id", (pid,)
    ).fetchall()
    return render_template("shop/product.html", p=p, images=images)


@app.route("/cart")
def cart_view():
    cart = session.get("cart", {})
    items, total = store.load_cart_items(g.db, cart)
    session["cart"] = cart
    min_amount = int(g.settings.get("min_order_amount", "0") or 0)
    return render_template("shop/cart.html", items=items, total=total,
                           min_amount=min_amount, shortage=max(0, min_amount - total))


@app.route("/cart/add", methods=["POST"])
def cart_add():
    pid = request.form.get("product_id", "")
    try:
        qty = max(1, int(request.form.get("qty", 1)))
    except ValueError:
        qty = 1
    p = g.db.execute("SELECT * FROM product WHERE id = ? AND is_active = 1", (pid,)).fetchone()
    if not p or not store.product_on_sale(p):
        flash("此商品目前未販售")
        return redirect(url_for("index"))
    cart = session.get("cart", {})
    cart[str(pid)] = int(cart.get(str(pid), 0)) + qty
    if p["stock_qty"] is not None:
        cart[str(pid)] = min(cart[str(pid)], p["stock_qty"])
    session["cart"] = cart
    flash(f"已加入「{p['name']}」")
    return redirect(request.form.get("back") or url_for("index"))


@app.route("/cart/update", methods=["POST"])
def cart_update():
    cart = session.get("cart", {})
    for key, value in request.form.items():
        if key.startswith("qty_"):
            pid = key[4:]
            try:
                qty = int(value)
            except ValueError:
                continue
            if qty <= 0:
                cart.pop(pid, None)
            else:
                cart[pid] = qty
    remove = request.form.get("remove")
    if remove:
        cart.pop(remove, None)
    session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart = session.get("cart", {})
    items, total = store.load_cart_items(g.db, cart)
    session["cart"] = cart
    if not items:
        flash("購物車是空的,先去逛逛吧!")
        return redirect(url_for("index"))
    pickup_points = g.db.execute(
        "SELECT * FROM pickup_point WHERE is_active = 1 ORDER BY sort_order, id"
    ).fetchall()
    min_amount = int(g.settings.get("min_order_amount", "0") or 0)

    methods = []
    if g.settings.get("enable_transfer", "1") == "1":
        methods.append("transfer")
    if g.settings.get("enable_cod", "1") == "1":
        methods.append("cod")
    if ecpay.enabled():
        methods.append("ecpay")

    if request.method == "POST":
        name = request.form.get("customer_name", "").strip()
        phone = request.form.get("phone", "").strip()
        method = request.form.get("payment_method", "")
        errors = []
        if not name:
            errors.append("請填寫姓名")
        if not re.fullmatch(r"09\d{8}", phone):
            errors.append("請填寫正確的手機號碼(09 開頭 10 碼)")
        if method not in methods:
            errors.append("請選擇付款方式")
        if errors:
            for e in errors:
                flash(e)
        else:
            try:
                order = store.create_order(request.form, cart, method)
            except store.OrderError as e:
                flash(str(e))
            else:
                session["cart"] = {}
                session["last_order"] = {"order_no": order["order_no"], "phone": phone}
                # LINE 通知管理員
                if config.line_enabled() and config.LINE_ADMIN_USER_ID:
                    lineapi.notify_admin(
                        f"🛒 新訂單 {order['order_no']}\n"
                        f"{name} / {phone}\n"
                        f"取貨點:{order['pickup_point_name']}\n"
                        f"金額:NT${order['total_amount']}\n"
                        f"付款:{store.PAYMENT_METHOD.get(method, method)}\n"
                        f"{base_url()}/admin/orders/{order['id']}"
                    )
                if method == "ecpay":
                    return redirect(url_for("pay_ecpay", order_no=order["order_no"]))
                return redirect(url_for("order_complete", order_no=order["order_no"]))

    return render_template("shop/checkout.html", items=items, total=total,
                           pickup_points=pickup_points, methods=methods,
                           min_amount=min_amount)


@app.route("/order/complete/<order_no>")
def order_complete(order_no):
    order = g.db.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
    last = session.get("last_order", {})
    if not order or last.get("order_no") != order_no:
        return redirect(url_for("index"))
    items = g.db.execute(
        "SELECT * FROM order_item WHERE order_id = ?", (order["id"],)
    ).fetchall()
    return render_template("shop/order_complete.html", order=order, items=items)


@app.route("/order/report-transfer", methods=["POST"])
def report_transfer():
    """轉帳後回報帳號末五碼。"""
    order_no = request.form.get("order_no", "").strip()
    phone = request.form.get("phone", "").strip()
    last5 = request.form.get("last5", "").strip()
    order = g.db.execute(
        "SELECT * FROM orders WHERE order_no = ? AND phone = ?", (order_no, phone)
    ).fetchone()
    if not order:
        flash("找不到符合的訂單,請確認訂單編號與手機號碼")
        return redirect(url_for("order_lookup"))
    if not re.fullmatch(r"\d{5}", last5):
        flash("請填寫轉帳帳號末五碼(5 位數字)")
        return redirect(url_for("order_lookup", order_no=order_no, phone=phone))
    g.db.execute(
        "UPDATE orders SET transfer_last5 = ?, payment_status = 'pending_check', "
        "updated_at = ? WHERE id = ?", (last5, now_tw(), order["id"]),
    )
    g.db.commit()
    if config.line_enabled() and config.LINE_ADMIN_USER_ID:
        lineapi.notify_admin(f"💰 訂單 {order_no} 回報轉帳末五碼:{last5},請核帳")
    flash("已收到您的轉帳回報,我們會盡快核對!")
    return redirect(url_for("order_lookup", order_no=order_no, phone=phone))


@app.route("/order/lookup", methods=["GET", "POST"])
def order_lookup():
    order = items = None
    order_no = request.values.get("order_no", "").strip()
    phone = request.values.get("phone", "").strip()
    if order_no and phone:
        order = g.db.execute(
            "SELECT * FROM orders WHERE order_no = ? AND phone = ?", (order_no, phone)
        ).fetchone()
        if order:
            items = g.db.execute(
                "SELECT * FROM order_item WHERE order_id = ?", (order["id"],)
            ).fetchall()
        else:
            flash("找不到符合的訂單,請確認訂單編號與手機號碼")
    return render_template("shop/order_lookup.html", order=order, items=items,
                           order_no=order_no, phone=phone)


# ---------------------------------------------------------------- 綠界金流

@app.route("/pay/ecpay/<order_no>")
def pay_ecpay(order_no):
    if not ecpay.enabled():
        abort(404)
    order = g.db.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
    last = session.get("last_order", {})
    if not order or last.get("order_no") != order_no:
        return redirect(url_for("index"))
    if order["payment_status"] == "paid":
        return redirect(url_for("order_complete", order_no=order_no))
    items = g.db.execute(
        "SELECT * FROM order_item WHERE order_id = ?", (order["id"],)
    ).fetchall()
    item_desc = "#".join(f"{it['product_name']}x{it['qty']}" for it in items)
    params = ecpay.build_payment_params(
        order_no, order["total_amount"], item_desc,
        return_url=f"{base_url()}/webhook/ecpay",
        client_back_url=f"{base_url()}/order/complete/{order_no}",
    )
    return render_template("shop/ecpay_redirect.html",
                           gateway_url=ecpay.gateway_url(), params=params)


@app.route("/webhook/ecpay", methods=["POST"])
def webhook_ecpay():
    """綠界背景付款通知。務必驗證 CheckMacValue 簽章。"""
    form = request.form.to_dict()
    if not ecpay.verify_notification(form):
        log.warning("ECPay webhook 簽章驗證失敗: %s", form.get("MerchantTradeNo"))
        return "0|CheckMacValue Error"
    order_no = form.get("CustomField1", "")
    order = g.db.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
    if not order:
        return "0|Order Not Found"
    rtn_code = form.get("RtnCode", "")
    paid = rtn_code == "1" and int(form.get("TradeAmt", 0)) == order["total_amount"]
    g.db.execute(
        """INSERT INTO payment_record(order_id, method, amount, gateway_txn_id,
           raw_data, status, created_at) VALUES (?, 'ecpay', ?, ?, ?, ?, ?)""",
        (order["id"], int(form.get("TradeAmt", 0)), form.get("TradeNo", ""),
         json.dumps(form, ensure_ascii=False), "paid" if paid else f"rtn={rtn_code}", now_tw()),
    )
    if paid and order["payment_status"] != "paid":
        g.db.execute(
            "UPDATE orders SET payment_status = 'paid', updated_at = ? WHERE id = ?",
            (now_tw(), order["id"]),
        )
        if config.line_enabled() and config.LINE_ADMIN_USER_ID:
            lineapi.notify_admin(f"✅ 訂單 {order_no} 已完成線上付款 NT${order['total_amount']}")
        if order["line_user_id"]:
            lineapi.push_text(order["line_user_id"],
                              f"✅ 您的訂單 {order_no} 已付款成功,我們會盡快為您備貨!")
    g.db.commit()
    return "1|OK"


# ---------------------------------------------------------------- LINE Webhook

LOOKUP_PATTERN = re.compile(r"(?:查詢\s*)?([A-Za-z]{1,4}\d{6,12})\s*(\d{3,10})?")

HELP_TEXT = (
    "您好!我是 Freshbox 訂購小幫手 🤖\n\n"
    "🔍 查詢訂單:\n"
    "傳「查詢 訂單編號 手機末三碼」\n"
    "例如:查詢 FB260704001 123\n\n"
    "查詢成功後,到貨時會自動傳 LINE 通知您!"
)


@app.route("/webhook/line", methods=["POST"])
def webhook_line():
    body = request.get_data()
    if not lineapi.verify_signature(body, request.headers.get("X-Line-Signature", "")):
        abort(403)
    payload = request.get_json(silent=True) or {}
    for event in payload.get("events", []):
        reply_token = event.get("replyToken", "")
        user_id = event.get("source", {}).get("userId", "")
        etype = event.get("type")
        if etype == "follow":
            shop = g.settings.get("shop_name", "Freshbox")
            lineapi.reply_text(reply_token,
                               f"歡迎加入 {shop}!\n\n"
                               f"🛒 線上下單:{base_url()}\n\n{HELP_TEXT}")
        elif etype == "message" and event.get("message", {}).get("type") == "text":
            text = event["message"].get("text", "").strip()
            m = LOOKUP_PATTERN.search(text)
            if m:
                lineapi.reply_text(reply_token,
                                   _handle_order_lookup(m.group(1).upper(), m.group(2), user_id))
            else:
                lineapi.reply_text(reply_token, HELP_TEXT)
    return "OK"


def _handle_order_lookup(order_no: str, phone_tail, user_id: str) -> str:
    order = g.db.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
    if not order:
        return f"找不到訂單 {order_no},請確認編號是否正確。"
    if not phone_tail:
        return "為保護個資,請在訂單編號後加上訂購時的手機末三碼,例如:\n" \
               f"查詢 {order_no} {'x' * 3}"
    if not order["phone"].endswith(phone_tail):
        return "手機號碼末碼不符,無法查詢此訂單。"
    if user_id and order["line_user_id"] != user_id:
        g.db.execute("UPDATE orders SET line_user_id = ? WHERE id = ?",
                     (user_id, order["id"]))
        g.db.commit()
    text = store.order_summary_text(g.db, order, g.settings)
    return text + "\n\n📲 已為您綁定 LINE 通知,到貨時會通知您!"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
