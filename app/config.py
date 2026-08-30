import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _base_domain_from_url(url: str) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").lower()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./serves.db")

    APPS_DIR: Path = Path(os.getenv("APPS_DIR", "./data/apps")).resolve()
    LOGS_DIR: Path = Path(os.getenv("LOGS_DIR", "./data/logs")).resolve()

    FREE_MAX_APPS: int = int(os.getenv("FREE_MAX_APPS", "1"))
    FREE_MEMORY_MB: int = int(os.getenv("FREE_MEMORY_MB", "256"))
    FREE_CPU_CORES: float = float(os.getenv("FREE_CPU_CORES", "0.5"))
    FREE_DISK_MB: int = int(os.getenv("FREE_DISK_MB", "2048"))
    # אחרי כמה ימים מההרשמה תוכנית חינמית מפסיקה לעבוד (אפליקציות נעצרות
    # ולא ניתנות להפעלה) עד שדרוג. 0 מבטל את המגבלה (חינם לצמיתות).
    FREE_TRIAL_DAYS: int = int(os.getenv("FREE_TRIAL_DAYS", "14"))

    # מגבלת גודל קובץ zip שאפשר להעלות כמקור קוד (חלופה לריפו git) - נבדקת
    # לפני שה-volume המוגבל-דיסק לפי תוכנית נוצר, אז זו הגנה נפרדת משלה
    # (לא תלויה בתוכנית של המשתמש).
    MAX_ZIP_UPLOAD_MB: int = int(os.getenv("MAX_ZIP_UPLOAD_MB", "200"))

    # הגבלת רוחב פס פר-אפליקציה לפי תוכנית (ראה app/services/bandwidth.py) -
    # דורש tc+ifb זמינים בשרת (root). אפשר לכבות לגמרי אם יש בעיה בשרת.
    BANDWIDTH_LIMIT_ENABLED: bool = _bool("BANDWIDTH_LIMIT_ENABLED", True)
    FREE_BANDWIDTH_MBPS: int = int(os.getenv("FREE_BANDWIDTH_MBPS", "5"))

    SANDBOX_NETWORK: str = os.getenv("SANDBOX_NETWORK", "serves_sandbox")
    SANDBOX_SUBNET: str = os.getenv("SANDBOX_SUBNET", "172.30.0.0/24")
    BASE_IMAGE: str = os.getenv("BASE_IMAGE", "serves-python-base:3.11")

    # חייב להתאים בדיוק ל-uid/gid של botuser ב-docker/base.Dockerfile
    SANDBOX_UID: int = int(os.getenv("SANDBOX_UID", "1000"))
    SANDBOX_GID: int = int(os.getenv("SANDBOX_GID", "1000"))

    # פורט קבוע (שמור) שמוזרק כמשתנה סביבה PORT לכל אפליקציה - אם הקוד
    # של המשתמש מריץ שרת אינטרנט, עליו להאזין על הפורט הזה (ועל 0.0.0.0,
    # לא רק 127.0.0.1) כדי שהפלטפורמה תוכל להזרים אליו תעבורה ב-/open/<id>.
    APP_PORT: int = int(os.getenv("APP_PORT", "8080"))

    PORT: int = int(os.getenv("PORT", "8000"))

    DISABLE_DOCKER: bool = _bool("DISABLE_DOCKER", False)

    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "TeleBoss <no-reply@boss-server-bot.online>")
    # STARTTLS (פורט 587, ברירת מחדל) מול SSL מוצפן-מהתחלה (פורט 465).
    # אם 587 חסום ע"י הספק, 465+SSL לרוב עוקף את זה - זו בדיוק השיטה
    # שעובדת בסקריפט ה-PHP שהוכיח שהחיבור לג'ימייל בכלל אפשרי מהשרת.
    SMTP_USE_TLS: bool = _bool("SMTP_USE_TLS", True)
    SMTP_USE_SSL: bool = _bool("SMTP_USE_SSL", False)

    VERIFICATION_CODE_TTL_MINUTES: int = int(os.getenv("VERIFICATION_CODE_TTL_MINUTES", "10"))
    VERIFICATION_RESEND_COOLDOWN_SECONDS: int = int(os.getenv("VERIFICATION_RESEND_COOLDOWN_SECONDS", "60"))
    REQUIRE_EMAIL_VERIFICATION: bool = _bool("REQUIRE_EMAIL_VERIFICATION", True)

    # הגבלות קצב (הגנה בסיסית נגד ניצול לרעה, לא Redis - ראו app/services/rate_limit.py)
    SIGNUP_IP_MAX_PER_DAY: int = int(os.getenv("SIGNUP_IP_MAX_PER_DAY", "3"))
    DEPLOY_ACTION_MAX: int = int(os.getenv("DEPLOY_ACTION_MAX", "5"))
    DEPLOY_ACTION_WINDOW_SECONDS: int = int(os.getenv("DEPLOY_ACTION_WINDOW_SECONDS", "60"))

    # בוט תשלומים נפרד (Telegram Stars) - לא חובה למלא אם אין תוכניות בתשלום.
    # רץ כ-background task בתוך אותו תהליך (long polling) - לא צריך שירות נפרד.
    PAYMENT_BOT_TOKEN: str = os.getenv("PAYMENT_BOT_TOKEN", "")
    PAYMENT_BOT_USERNAME: str = os.getenv("PAYMENT_BOT_USERNAME", "")
    PAYMENT_LINK_TTL_MINUTES: int = int(os.getenv("PAYMENT_LINK_TTL_MINUTES", "30"))

    # כתובת הבסיס הציבורית של האתר (לקישור "פתח דשבורד" בהודעת אישור התשלום בבוט,
    # וגם לזיהוי סאב-דומיין של אפליקציה - ראו APPS_BASE_DOMAIN למטה)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")

    @property
    def APPS_BASE_DOMAIN(self) -> str:
        # אם מוגדר PUBLIC_BASE_URL (למשל https://teleboss.online), אפליקציות
        # נגישות גם תחת <slug>.<דומיין> ולא רק /open/<slug> - בתנאי שיש
        # רשומת DNS wildcard (*.teleboss.online) ותעודת SSL wildcard מוגדרות
        # בשרת. אם ה-DNS לא מוגדר, בקשות לסאב-דומיין כזה פשוט לא יגיעו
        # לשרת מלכתחילה - אז אין נזק להשאיר את זה דלוק תמיד כש-PUBLIC_BASE_URL קיים.
        return _base_domain_from_url(self.PUBLIC_BASE_URL)

    # פאנל ניהול באתר - נתיב סודי (לא מקושר משום מקום) + התחברות נפרדת
    # משלי (לא קשור לחשבונות משתמשים רגילים). אם ADMIN_USERNAME/ADMIN_PASSWORD
    # ריקים, הפאנל כבוי לגמרי (404 על כל נתיב שלו).
    ADMIN_PATH: str = os.getenv("ADMIN_PATH", "admin997799").strip("/") or "admin997799"
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

    # מזהי משתמש טלגרם (לא username - המספר המספרי) שמורשים להשתמש בפקודת
    # /admin בבוט התשלומים. אפשר לקבל את המזהה שלך מ-@userinfobot בטלגרם.
    ADMIN_TELEGRAM_IDS: frozenset[int] = frozenset(
        int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",") if x
    )


settings = Settings()


def _plan_resource(prefix: str, name: str, cast, default: str) -> int | float:
    # PRO/PLUS יורשים ברירת מחדל ממגבלות התוכנית החינמית אם לא הוגדר
    # ערך ייעודי - כלומר "כמו החינם" עד שמגדירים אחרת ב-.env.
    return cast(os.getenv(f"{prefix}_{name}", default))


# הגדרת התוכניות: לכל תוכנית - מקסימום אפליקציות, מחיר בכוכבי טלגרם,
# וכמות המשאבים (זיכרון/CPU/דיסק) לכל אפליקציה בתוכנית הזו.
PLANS: dict[str, dict] = {
    "free": {
        "max_apps": settings.FREE_MAX_APPS,
        "stars": 0,
        "memory_mb": settings.FREE_MEMORY_MB,
        "cpu_cores": settings.FREE_CPU_CORES,
        "disk_mb": settings.FREE_DISK_MB,
        "bandwidth_mbps": settings.FREE_BANDWIDTH_MBPS,
    },
    "pro": {
        "max_apps": int(os.getenv("PRO_MAX_APPS", "3")),
        "stars": int(os.getenv("PRO_PLAN_STARS", "1000")),
        "memory_mb": _plan_resource("PRO", "MEMORY_MB", int, str(settings.FREE_MEMORY_MB)),
        "cpu_cores": _plan_resource("PRO", "CPU_CORES", float, str(settings.FREE_CPU_CORES)),
        "disk_mb": _plan_resource("PRO", "DISK_MB", int, str(settings.FREE_DISK_MB)),
        "bandwidth_mbps": _plan_resource("PRO", "BANDWIDTH_MBPS", int, str(settings.FREE_BANDWIDTH_MBPS * 3)),
    },
    "plus": {
        "max_apps": int(os.getenv("PLUS_MAX_APPS", "5")),
        "stars": int(os.getenv("PLUS_PLAN_STARS", "2500")),
        "memory_mb": _plan_resource("PLUS", "MEMORY_MB", int, "1024"),
        "cpu_cores": _plan_resource("PLUS", "CPU_CORES", float, "1.0"),
        "disk_mb": _plan_resource("PLUS", "DISK_MB", int, "8192"),
        "bandwidth_mbps": _plan_resource("PLUS", "BANDWIDTH_MBPS", int, "100"),
    },
}

settings.APPS_DIR.mkdir(parents=True, exist_ok=True)
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
