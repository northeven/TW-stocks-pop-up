"""SQLite 資料層:連線、schema 建立與「寬容式」升級。

設計原則:
- CREATE TABLE IF NOT EXISTS + 自動補缺少的欄位,
  所以把舊原型的 freshbox.db 直接丟進 data/ 也能無痛升級,不會蓋掉既有資料。
- 首次啟動(空資料庫)自動建立預設分類、5 個取貨點、系統設定與示範商品。
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import config

TW_TZ = timezone(timedelta(hours=8))


def now_tw() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_tw() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ---------------------------------------------------------------- schema

SCHEMA = {
    "category": """
        CREATE TABLE IF NOT EXISTS category (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        )""",
    "product": """
        CREATE TABLE IF NOT EXISTS product (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            description TEXT DEFAULT '',
            price INTEGER DEFAULT 0,
            original_price INTEGER,
            image_url TEXT DEFAULT '',
            stock_qty INTEGER,
            sold_qty INTEGER DEFAULT 0,
            tag TEXT DEFAULT '',
            is_limited INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            sell_start TEXT DEFAULT '',
            sell_end TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )""",
    "product_image": """
        CREATE TABLE IF NOT EXISTS product_image (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        )""",
    "pickup_point": """
        CREATE TABLE IF NOT EXISTS pickup_point (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT DEFAULT '',
            note TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        )""",
    "setting": """
        CREATE TABLE IF NOT EXISTS setting (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )""",
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL UNIQUE,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            pickup_point_id INTEGER,
            pickup_point_name TEXT DEFAULT '',
            payment_method TEXT DEFAULT 'transfer',
            payment_status TEXT DEFAULT 'unpaid',
            order_status TEXT DEFAULT 'pending',
            total_amount INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            admin_note TEXT DEFAULT '',
            transfer_last5 TEXT DEFAULT '',
            line_user_id TEXT DEFAULT '',
            arrived_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )""",
    "order_item": """
        CREATE TABLE IF NOT EXISTS order_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER,
            product_name TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            qty INTEGER DEFAULT 1,
            unit_price INTEGER DEFAULT 0,
            subtotal INTEGER DEFAULT 0
        )""",
    "payment_record": """
        CREATE TABLE IF NOT EXISTS payment_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            method TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            gateway_txn_id TEXT DEFAULT '',
            raw_data TEXT DEFAULT '',
            status TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )""",
    "admin_user": """
        CREATE TABLE IF NOT EXISTS admin_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            must_change INTEGER DEFAULT 0,
            created_at TEXT DEFAULT ''
        )""",
    "status_log": """
        CREATE TABLE IF NOT EXISTS status_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            field TEXT DEFAULT 'order_status',
            from_value TEXT DEFAULT '',
            to_value TEXT DEFAULT '',
            operator TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )""",
}

# 舊資料庫升級時要補的欄位(表名 -> {欄位: 型別定義})
EXPECTED_COLUMNS = {
    "product": {
        "description": "TEXT DEFAULT ''",
        "original_price": "INTEGER",
        "sold_qty": "INTEGER DEFAULT 0",
        "tag": "TEXT DEFAULT ''",
        "is_limited": "INTEGER DEFAULT 0",
        "sort_order": "INTEGER DEFAULT 0",
        "sell_start": "TEXT DEFAULT ''",
        "sell_end": "TEXT DEFAULT ''",
        "created_at": "TEXT DEFAULT ''",
        "stock_qty": "INTEGER",
        "image_url": "TEXT DEFAULT ''",
        "spec": "TEXT DEFAULT ''",
    },
    "orders": {
        "pickup_point_name": "TEXT DEFAULT ''",
        "admin_note": "TEXT DEFAULT ''",
        "transfer_last5": "TEXT DEFAULT ''",
        "line_user_id": "TEXT DEFAULT ''",
        "arrived_at": "TEXT DEFAULT ''",
        "updated_at": "TEXT DEFAULT ''",
        "payment_status": "TEXT DEFAULT 'unpaid'",
        "payment_method": "TEXT DEFAULT 'transfer'",
    },
    "order_item": {
        "product_name": "TEXT DEFAULT ''",
        "spec": "TEXT DEFAULT ''",
        "subtotal": "INTEGER DEFAULT 0",
    },
    "pickup_point": {"sort_order": "INTEGER DEFAULT 0"},
    "category": {"sort_order": "INTEGER DEFAULT 0", "is_active": "INTEGER DEFAULT 1"},
    "admin_user": {"must_change": "INTEGER DEFAULT 0"},
}

DEFAULT_SETTINGS = {
    "shop_name": "Vitaslow Freshbox™",
    "shop_intro": "EatAsia 旗下辦公室團購生鮮品牌|嚴選 7 大品牌,新鮮直送辦公室",
    "announcement": "",
    "min_order_amount": "300",
    "preorder_days": "3-5",
    "pickup_deadline_days": "3",
    "order_prefix": "FB",
    "line_url": "",
    "bank_info": "銀行:(請至後台系統設定填寫)\n帳號:\n戶名:",
    "enable_cod": "1",
    "enable_transfer": "1",
}

DEFAULT_PICKUP_POINTS = ["南港", "汐止", "汐科", "內湖", "淡水"]

DEFAULT_CATEGORIES = ["人氣麵包", "主廚料理", "冷凍即食", "生鮮雞肉", "手工抹醬", "養生補品", "Haloé 烘焙"]

DEMO_PRODUCTS = [
    ("人氣麵包", "小真實 TRUE BAKERY 日式生吐司", "1 條(約 450g)", 120, "人氣", 30),
    ("人氣麵包", "小真實 TRUE BAKERY 生奶包", "6 入/袋", 150, "人氣", 30),
    ("人氣麵包", "小真實 TRUE BAKERY 爆餡包(芋泥)", "4 入/盒", 180, "限量", 20),
    ("Haloé 烘焙", "Haloé 手工餅乾禮盒", "12 片/盒", 320, "禮盒", None),
    ("Haloé 烘焙", "Haloé 迷你小泡芙", "10 入/盒", 160, "", None),
    ("手工抹醬", "花生醬(顆粒)", "350g/罐", 220, "", None),
    ("冷凍即食", "舒肥雞胸肉(原味)", "180g×2/包", 130, "人氣", 50),
]


def _table_columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def init_db():
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # DATA_DIR 不可寫(例如還沒掛磁碟)時退回程式目錄,避免整站起不來
        import logging
        logging.getLogger("freshbox").warning(
            "DATA_DIR %s 無法建立,改用 %s(注意:此目錄可能不是持久儲存)",
            config.DATA_DIR, config.BASE_DIR / "data")
        config.DATA_DIR = config.BASE_DIR / "data"
        config.DB_PATH = config.DATA_DIR / "freshbox.db"
        config.UPLOAD_DIR = config.DATA_DIR / "uploads"
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        for ddl in SCHEMA.values():
            conn.execute(ddl)
        # 寬容式升級:補上舊資料庫缺少的欄位
        for table, cols in EXPECTED_COLUMNS.items():
            existing = _table_columns(conn, table)
            for col, coldef in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_item_order ON order_item(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_product_cat ON product(category_id)")

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO setting(key, value) VALUES (?, ?)", (key, value))

        if conn.execute("SELECT COUNT(*) c FROM pickup_point").fetchone()["c"] == 0:
            for i, name in enumerate(DEFAULT_PICKUP_POINTS):
                conn.execute(
                    "INSERT INTO pickup_point(name, sort_order) VALUES (?, ?)", (name, i)
                )

        if conn.execute("SELECT COUNT(*) c FROM category").fetchone()["c"] == 0:
            for i, name in enumerate(DEFAULT_CATEGORIES):
                conn.execute(
                    "INSERT INTO category(name, sort_order) VALUES (?, ?)", (name, i)
                )
            # 只有全新資料庫才放示範商品,方便第一次打開就看得到畫面
            for cat_name, name, spec, price, tag, stock in DEMO_PRODUCTS:
                cat = conn.execute(
                    "SELECT id FROM category WHERE name = ?", (cat_name,)
                ).fetchone()
                conn.execute(
                    """INSERT INTO product(category_id, name, spec, price, tag,
                       stock_qty, is_limited, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cat["id"], name, spec, price, tag, stock,
                     1 if tag == "限量" else 0, now_tw()),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- helpers

def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM setting")}


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO setting(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def next_order_no(conn, prefix="FB") -> str:
    """訂單編號:前綴 + 年月日 + 3 位流水號,例如 FB260704001。"""
    datestr = datetime.now(TW_TZ).strftime("%y%m%d")
    base = f"{prefix}{datestr}"
    row = conn.execute(
        "SELECT order_no FROM orders WHERE order_no LIKE ? ORDER BY order_no DESC LIMIT 1",
        (base + "%",),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row["order_no"][len(base):]) + 1
        except ValueError:
            seq = 1
    return f"{base}{seq:03d}"


def log_status(conn, order_id, field, from_value, to_value, operator=""):
    conn.execute(
        """INSERT INTO status_log(order_id, field, from_value, to_value, operator, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (order_id, field, str(from_value or ""), str(to_value or ""), operator, now_tw()),
    )
