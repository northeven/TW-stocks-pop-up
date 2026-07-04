"""商業邏輯:訂單狀態機、購物車計算、建立訂單(含防超賣)。"""
from db import get_db, now_tw, today_tw, next_order_no, log_status, get_settings

# 訂單狀態機(依規劃書 3.5)
ORDER_STATUS = {
    "pending": "待確認",
    "confirmed": "已確認",
    "preparing": "備貨中",
    "shipped": "已出貨",
    "ready": "待取貨",
    "done": "已完成",
    "overdue": "逾期未取",
    "cancelled": "已取消",
}
# 一般流程順序(後台會顯示「推進到下一步」按鈕)
STATUS_FLOW = ["pending", "confirmed", "preparing", "shipped", "ready", "done"]

PAYMENT_STATUS = {
    "unpaid": "未付款",
    "pending_check": "待核對",
    "paid": "已付款",
    "refunded": "已退款",
}

PAYMENT_METHOD = {
    "transfer": "轉帳匯款",
    "cod": "取貨付款",
    "ecpay": "線上付款",
}


class OrderError(Exception):
    """下單失敗時丟給前端顯示的訊息。"""


def product_on_sale(p, today=None) -> bool:
    """商品是否在可售狀態(上架 + 在販售區間內 + 有庫存)。"""
    if not p["is_active"]:
        return False
    today = today or today_tw()
    if p["sell_start"] and today < p["sell_start"]:
        return False
    if p["sell_end"] and today > p["sell_end"]:
        return False
    if p["stock_qty"] is not None and p["stock_qty"] <= 0:
        return False
    return True


def load_cart_items(conn, cart: dict):
    """cart = {product_id(str): qty}; 回傳 (items, total)。自動剔除已下架商品。"""
    items, total = [], 0
    for pid, qty in list(cart.items()):
        try:
            qty = max(0, int(qty))
        except (TypeError, ValueError):
            qty = 0
        p = conn.execute("SELECT * FROM product WHERE id = ?", (pid,)).fetchone()
        if not p or qty <= 0 or not product_on_sale(p):
            cart.pop(pid, None)
            continue
        if p["stock_qty"] is not None:
            qty = min(qty, p["stock_qty"])
            cart[pid] = qty
        subtotal = p["price"] * qty
        total += subtotal
        items.append({"product": p, "qty": qty, "subtotal": subtotal})
    return items, total


def create_order(form: dict, cart: dict, payment_method: str):
    """建立訂單。用 BEGIN IMMEDIATE 交易 + 庫存檢查防止超賣。"""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        settings = get_settings(conn)
        items, total = load_cart_items(conn, dict(cart))
        if not items:
            raise OrderError("購物車是空的,請先選購商品")
        min_amount = int(settings.get("min_order_amount", "0") or 0)
        if total < min_amount:
            raise OrderError(f"未達最低訂購金額 NT${min_amount},還差 NT${min_amount - total}")

        pickup = conn.execute(
            "SELECT * FROM pickup_point WHERE id = ? AND is_active = 1",
            (form.get("pickup_point_id"),),
        ).fetchone()
        if not pickup:
            raise OrderError("請選擇取貨點")

        # 防超賣:再次確認庫存並扣減
        for it in items:
            p = conn.execute(
                "SELECT * FROM product WHERE id = ?", (it["product"]["id"],)
            ).fetchone()
            if p["stock_qty"] is not None:
                if p["stock_qty"] < it["qty"]:
                    raise OrderError(f"「{p['name']}」庫存不足(剩 {p['stock_qty']} 件),請調整數量")
                conn.execute(
                    "UPDATE product SET stock_qty = stock_qty - ?, sold_qty = sold_qty + ? WHERE id = ?",
                    (it["qty"], it["qty"], p["id"]),
                )
            else:
                conn.execute(
                    "UPDATE product SET sold_qty = sold_qty + ? WHERE id = ?",
                    (it["qty"], p["id"]),
                )

        order_no = next_order_no(conn, settings.get("order_prefix", "FB"))
        payment_status = "unpaid"
        cur = conn.execute(
            """INSERT INTO orders(order_no, customer_name, phone, pickup_point_id,
               pickup_point_name, payment_method, payment_status, order_status,
               total_amount, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (order_no, form["customer_name"].strip(), form["phone"].strip(),
             pickup["id"], pickup["name"], payment_method, payment_status,
             total, form.get("note", "").strip(), now_tw(), now_tw()),
        )
        order_id = cur.lastrowid
        for it in items:
            p = it["product"]
            conn.execute(
                """INSERT INTO order_item(order_id, product_id, product_name, spec,
                   qty, unit_price, subtotal) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (order_id, p["id"], p["name"], p["spec"], it["qty"], p["price"], it["subtotal"]),
            )
        log_status(conn, order_id, "order_status", "", "pending", "customer")
        conn.commit()
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def restock_order(conn, order_id):
    """取消訂單時把庫存加回去。"""
    for it in conn.execute("SELECT * FROM order_item WHERE order_id = ?", (order_id,)):
        if it["product_id"]:
            conn.execute(
                """UPDATE product SET
                   stock_qty = CASE WHEN stock_qty IS NULL THEN NULL ELSE stock_qty + ? END,
                   sold_qty = MAX(0, sold_qty - ?) WHERE id = ?""",
                (it["qty"], it["qty"], it["product_id"]),
            )


def order_summary_text(conn, order, settings) -> str:
    """給 LINE 通知/查詢用的訂單文字摘要。"""
    items = conn.execute(
        "SELECT * FROM order_item WHERE order_id = ?", (order["id"],)
    ).fetchall()
    lines = [f"訂單 {order['order_no']}"]
    lines.append(f"狀態:{ORDER_STATUS.get(order['order_status'], order['order_status'])}"
                 f" / {PAYMENT_STATUS.get(order['payment_status'], order['payment_status'])}")
    for it in items:
        lines.append(f"・{it['product_name']} x{it['qty']} = ${it['subtotal']}")
    lines.append(f"合計 NT${order['total_amount']}")
    lines.append(f"取貨點:{order['pickup_point_name']}")
    if order["order_status"] == "ready":
        deadline = settings.get("pickup_deadline_days", "3")
        lines.append(f"⚠️ 商品已到貨,請於 {deadline} 天內取貨")
    return "\n".join(lines)
