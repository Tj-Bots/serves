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

    PORT: int = int(os.getenv("PORT", "8000"))

    DISABLE_DOCKER: bool = _bool("DISABLE_DOCKER", False)


settings = Settings()

settings.APPS_DIR.mkdir(parents=True, exist_ok=True)
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
