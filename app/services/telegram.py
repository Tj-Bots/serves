"""
מזהה אוטומטית אם אחד ממשתני הסביבה של האפליקציה הוא טוקן של בוט טלגרם,
כדי שנוכל להציג קישור ישיר לבוט (https://t.me/<username>) בעמוד האפליקציה -
בלי לדרוש מהמשתמש למלא שם משתנה ספציפי (TELEGRAM_BOT_TOKEN וכו').
"""

import json
import logging
import re
import urllib.request

TOKEN_PATTERN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,45}$")

logger = logging.getLogger("serves.telegram")


def find_bot_username(env_vars: dict) -> str | None:
    for value in (env_vars or {}).values():
        value = str(value).strip()
        if not TOKEN_PATTERN.match(value):
            continue
        username = _fetch_username(value)
        if username:
            return username
    return None


def _fetch_username(token: str) -> str | None:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.info("could not reach Telegram API to resolve bot username")
        return None

    if not data.get("ok"):
        return None
    return data.get("result", {}).get("username")
