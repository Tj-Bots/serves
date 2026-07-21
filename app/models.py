import datetime
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    accepted_terms_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    is_verified: Mapped[bool] = mapped_column(default=False)
    verification_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    verification_code_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    verification_sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    password_reset_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    password_reset_code_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    password_reset_sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    signup_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan: Mapped[str] = mapped_column(String(20), default="free")

    is_blocked: Mapped[bool] = mapped_column(default=False)
    warning_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    warning_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    apps: Mapped[list["BotApp"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class AppStatus(str, enum.Enum):
    PENDING = "pending"
    BUILDING = "building"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class BotApp(Base):
    __tablename__ = "bot_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(140), unique=True, nullable=True, index=True)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    requirements_file: Mapped[str] = mapped_column(String(255), default="requirements.txt")
    run_command: Mapped[str] = mapped_column(String(500), nullable=False)
    env_vars: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[AppStatus] = mapped_column(Enum(AppStatus), default=AppStatus.PENDING)
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    admin_suspended: Mapped[bool] = mapped_column(default=False)
    admin_suspend_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    owner: Mapped["User"] = relationship(back_populates="apps")


class PurchaseStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"


class PlanPurchase(Base):
    __tablename__ = "plan_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    pay_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    plan_name: Mapped[str] = mapped_column(String(20), nullable=False)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PurchaseStatus] = mapped_column(Enum(PurchaseStatus), default=PurchaseStatus.PENDING)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    paid_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class PromoCode(Base):
    """קוד מימוש שמעניק תוכנית בתשלום בחינם - נוצר ע"י מנהל דרך פקודת
    /admin בבוט (ראו app/services/payment_bot.py), ומוממש ע"י משתמש
    בעמוד /billing (ראו app/routers/billing.py)."""

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    plan_name: Mapped[str] = mapped_column(String(20), nullable=False)
    max_uses: Mapped[int] = mapped_column(default=1)
    used_count: Mapped[int] = mapped_column(default=0)
    created_by_telegram_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
