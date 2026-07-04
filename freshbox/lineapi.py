"""LINE Messaging API:推播通知 + Webhook 簽章驗證 + 訂單查詢機器人。

只用 Python 標準庫(urllib),不需要安裝 line-bot-sdk。
未設定金鑰時所有函式安全地跳過,不影響主流程。
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.request

import config

log = logging.getLogger("freshbox.line")

API_BASE = "https://api.line.me/v2/bot"


def _post(path: str, payload: dict) -> bool:
    if not config.LINE_CHANNEL_ACCESS_TOKEN:
        return False
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:  # 通知失敗不能擋下單流程
        log.warning("LINE API %s failed: %s", path, e)
        return False


def push_text(user_id: str, text: str) -> bool:
    if not user_id:
        return False
    return _post("/message/push", {
        "to": user_id,
        "messages": [{"type": "text", "text": text[:4900]}],
    })


def reply_text(reply_token: str, text: str) -> bool:
    return _post("/message/reply", {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    })


def notify_admin(text: str) -> bool:
    """新訂單等事件推播給管理員本人。"""
    return push_text(config.LINE_ADMIN_USER_ID, text)


def verify_signature(body: bytes, signature: str) -> bool:
    if not config.LINE_CHANNEL_SECRET:
        return False
    mac = hmac.new(config.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")
