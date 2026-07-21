import re

# שמות שאסור לתת ל-slug כי הם עלולים להתנגש עם סאב-דומיינים אמיתיים
# של האתר עצמו (למשל www.teleboss.online, api.teleboss.online) כשמופעל
# ניתוב לפי סאב-דומיין - ראו app.main.app_subdomain_proxy.
RESERVED_SLUGS = {"www", "api", "admin", "mail", "ftp", "app", "static"}


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "app"
