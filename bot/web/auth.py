"""Telegram Mini App initData tekshiruvi.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 24 * 3600


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """To'g'ri bo'lsa Telegram foydalanuvchisi (dict) qaytaradi, aks holda None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        if time.time() - int(pairs.get("auth_date", "0")) > MAX_AGE_SECONDS:
            return None
        user = json.loads(pairs.get("user", "null"))
    except (ValueError, TypeError):
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    return user


# --- Brauzer uchun shaxsiy havola -------------------------------------------
# Telegramdan tashqarida initData yo'q, shuning uchun bot /web buyrug'ida
# imzolangan kalit beradi: "<tg_id>.<tugash_vaqti>.<imzo>".

WEB_TOKEN_DAYS = 30


def _web_signature(payload: str, bot_token: str) -> str:
    secret = hashlib.sha256(f"web-login:{bot_token}".encode()).digest()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:40]


def make_web_token(tg_id: int, bot_token: str, days: int = WEB_TOKEN_DAYS) -> str:
    payload = f"{tg_id}.{int(time.time()) + days * 24 * 3600}"
    return f"{payload}.{_web_signature(payload, bot_token)}"


def validate_web_token(token: str, bot_token: str) -> int | None:
    """To'g'ri va muddati o'tmagan bo'lsa Telegram ID qaytaradi."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    payload = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_web_signature(payload, bot_token), parts[2]):
        return None
    if int(parts[1]) < time.time():
        return None
    return int(parts[0])
