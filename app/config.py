import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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

    TERMS_URL: str = os.getenv("TERMS_URL", "https://boss-server-bot.online/תקנון.html")

    FREE_MAX_APPS: int = int(os.getenv("FREE_MAX_APPS", "1"))
    FREE_MEMORY_MB: int = int(os.getenv("FREE_MEMORY_MB", "256"))
    FREE_CPU_CORES: float = float(os.getenv("FREE_CPU_CORES", "0.5"))
    FREE_DISK_MB: int = int(os.getenv("FREE_DISK_MB", "4096"))

    SANDBOX_NETWORK: str = os.getenv("SANDBOX_NETWORK", "serves_sandbox")
    SANDBOX_SUBNET: str = os.getenv("SANDBOX_SUBNET", "172.30.0.0/24")
    BASE_IMAGE: str = os.getenv("BASE_IMAGE", "serves-python-base:3.11")

    # חייב להתאים בדיוק ל-uid/gid של botuser ב-docker/base.Dockerfile
    SANDBOX_UID: int = int(os.getenv("SANDBOX_UID", "1000"))
    SANDBOX_GID: int = int(os.getenv("SANDBOX_GID", "1000"))

    PORT: int = int(os.getenv("PORT", "8000"))

    DISABLE_DOCKER: bool = _bool("DISABLE_DOCKER", False)

    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "Serves <no-reply@boss-server-bot.online>")
    # STARTTLS (פורט 587, ברירת מחדל) מול SSL מוצפן-מהתחלה (פורט 465).
    # אם 587 חסום ע"י הספק, 465+SSL לרוב עוקף את זה - זו בדיוק השיטה
    # שעובדת בסקריפט ה-PHP שהוכיח שהחיבור לג'ימייל בכלל אפשרי מהשרת.
    SMTP_USE_TLS: bool = _bool("SMTP_USE_TLS", True)
    SMTP_USE_SSL: bool = _bool("SMTP_USE_SSL", False)

    VERIFICATION_CODE_TTL_MINUTES: int = int(os.getenv("VERIFICATION_CODE_TTL_MINUTES", "10"))
    VERIFICATION_RESEND_COOLDOWN_SECONDS: int = int(os.getenv("VERIFICATION_RESEND_COOLDOWN_SECONDS", "60"))
    REQUIRE_EMAIL_VERIFICATION: bool = _bool("REQUIRE_EMAIL_VERIFICATION", True)


settings = Settings()

settings.APPS_DIR.mkdir(parents=True, exist_ok=True)
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
