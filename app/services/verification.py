import datetime
import logging
import secrets

from app.config import settings
from app.models import User
from app.services.email import send_verification_email

logger = logging.getLogger("serves.verification")


def generate_and_send_code(user: User) -> None:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.datetime.now(datetime.timezone.utc)
    user.verification_code = code
    user.verification_code_expires_at = now + datetime.timedelta(minutes=settings.VERIFICATION_CODE_TTL_MINUTES)
    user.verification_sent_at = now
    try:
        send_verification_email(user.email, code)
    except Exception:
        # הקוד כבר נשמר על המשתמש - אפשר לנסות "שליחה חוזרת" אחרי שמתקנים SMTP
        logger.error("could not send verification email to %s, code stored for retry", user.email)


def check_code(user: User, submitted_code: str) -> bool:
    if not user.verification_code or not user.verification_code_expires_at:
        return False
    expires_at = user.verification_code_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if datetime.datetime.now(datetime.timezone.utc) > expires_at:
        return False
    return secrets.compare_digest(submitted_code.strip(), user.verification_code)


def mark_verified(user: User) -> None:
    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None


def resend_cooldown_remaining(user: User) -> int:
    if not user.verification_sent_at:
        return 0
    sent_at = user.verification_sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=datetime.timezone.utc)
    elapsed = (datetime.datetime.now(datetime.timezone.utc) - sent_at).total_seconds()
    remaining = settings.VERIFICATION_RESEND_COOLDOWN_SECONDS - elapsed
    return max(0, int(remaining))
