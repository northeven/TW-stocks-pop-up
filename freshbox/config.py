"""集中管理所有環境變數設定。

正式部署時只要設定環境變數即可,不需要改任何程式碼。
本機開發時全部都有安全的預設值,直接 python3 app.py 就能跑。
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 資料存放目錄(資料庫 + 上傳圖片)。
# 部署到 Render/Zeabur 掛載持久磁碟時,把 DATA_DIR 指到磁碟掛載點即可。
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "freshbox.db"
UPLOAD_DIR = DATA_DIR / "uploads"

# Flask session 簽章金鑰。正式環境務必設定 SECRET_KEY,
# 否則每次重啟會重新產生,所有購物車與登入狀態會失效。
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# 後台初始密碼:第一次啟動時建立 admin 帳號用(會以雜湊儲存,不存明碼)。
# 登入後可在後台「修改密碼」頁面更換。
ADMIN_INIT_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD", "eatasia2026")

# 上傳限制
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_UPLOAD_MB = 8
COVER_SIZE = (800, 800)  # 商品封面圖裁切尺寸

# ---- LINE 官方帳號(Messaging API)----
# 兩組都設定後,系統自動啟用:
#   1. 新訂單即時推播給管理員(LINE_ADMIN_USER_ID)
#   2. /webhook/line 訂單查詢機器人(客人傳「查詢 訂單編號 手機末三碼」)
#   3. 到貨通知推播(客人查詢過訂單後即完成綁定)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_ADMIN_USER_ID = os.environ.get("LINE_ADMIN_USER_ID", "")

# ---- 綠界 ECPay 金流 ----
# 三組金鑰都設定後,結帳頁自動出現「線上付款(信用卡/ATM/超商代碼)」選項。
# ECPAY_STAGE=1 使用綠界測試環境(預設),正式上線時設 ECPAY_STAGE=0。
ECPAY_MERCHANT_ID = os.environ.get("ECPAY_MERCHANT_ID", "")
ECPAY_HASH_KEY = os.environ.get("ECPAY_HASH_KEY", "")
ECPAY_HASH_IV = os.environ.get("ECPAY_HASH_IV", "")
ECPAY_STAGE = os.environ.get("ECPAY_STAGE", "1") == "1"

# 對外網址(產生金流回呼、LINE 訊息內連結用),例如 https://shop.eatasia.com.tw
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def line_enabled() -> bool:
    return bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET)


def ecpay_enabled() -> bool:
    return bool(ECPAY_MERCHANT_ID and ECPAY_HASH_KEY and ECPAY_HASH_IV)
