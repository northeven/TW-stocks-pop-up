"""綠界 ECPay 全方位金流串接(信用卡 / ATM / 超商代碼)。

- 產生訂單付款表單(自動 POST 到綠界收銀台)
- CheckMacValue 簽章產生與「伺服器端」驗證(絕不只信前端回傳)
- 未設定金鑰時 enabled() 為 False,結帳頁不會出現線上付款選項
"""
import hashlib
import urllib.parse
from datetime import datetime

import config
from db import TW_TZ

STAGE_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
PROD_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"


def enabled() -> bool:
    return config.ecpay_enabled()


def gateway_url() -> str:
    return STAGE_URL if config.ECPAY_STAGE else PROD_URL


def _ecpay_urlencode(s: str) -> str:
    """綠界要求 .NET 風格的 URL encode(小寫 + 特定字元不轉)。"""
    encoded = urllib.parse.quote_plus(s).lower()
    for raw, seq in [("-", "%2d"), ("_", "%5f"), (".", "%2e"),
                     ("!", "%21"), ("*", "%2a"), ("(", "%28"), (")", "%29")]:
        encoded = encoded.replace(seq, raw)
    return encoded


def check_mac_value(params: dict) -> str:
    filtered = {k: v for k, v in params.items() if k != "CheckMacValue"}
    ordered = sorted(filtered.items(), key=lambda kv: kv[0].lower())
    raw = "&".join(f"{k}={v}" for k, v in ordered)
    raw = f"HashKey={config.ECPAY_HASH_KEY}&{raw}&HashIV={config.ECPAY_HASH_IV}"
    raw = _ecpay_urlencode(raw)
    return hashlib.sha256(raw.encode()).hexdigest().upper()


def build_payment_params(order_no: str, amount: int, item_desc: str,
                         return_url: str, client_back_url: str) -> dict:
    """產生自動送出表單所需的所有欄位(含 CheckMacValue)。

    綠界的 MerchantTradeNo 不能重複,所以加上時間戳後綴,
    回傳時再用 CustomField1 對回我們自己的訂單編號。
    """
    trade_no = f"{order_no}T{datetime.now(TW_TZ).strftime('%H%M%S')}"[:20]
    params = {
        "MerchantID": config.ECPAY_MERCHANT_ID,
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio",
        "TotalAmount": str(int(amount)),
        "TradeDesc": "Vitaslow Freshbox order",
        "ItemName": item_desc[:390] or "Freshbox order",
        "ReturnURL": return_url,          # 綠界伺服器背景通知(付款結果以此為準)
        "ClientBackURL": client_back_url,  # 消費者按「返回商店」的網址
        "ChoosePayment": "ALL",
        "EncryptType": "1",
        "CustomField1": order_no,
    }
    params["CheckMacValue"] = check_mac_value(params)
    return params


def verify_notification(form: dict) -> bool:
    """伺服器端驗證綠界回傳簽章,防止偽造付款通知。"""
    if "CheckMacValue" not in form:
        return False
    expected = check_mac_value(form)
    return expected == form.get("CheckMacValue", "").upper()
