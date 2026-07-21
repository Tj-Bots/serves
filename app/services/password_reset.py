import datetime
import logging
import secrets

from app.config import settings
from app.models import User
from app.services.email import InvalidRecipientError, send_password_reset_email

logger = logging.getLogger("serves.password_reset")


def generate_and_send_reset_code(user: User) -> None:
    """מעלה InvalidRecipientError אם הכתובת נדחתה ע"י שרת ה-SMTP."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.datetime.now(datetime.timezone.utc)
    user.password_reset_code = code
    user.password_reset_code_expires_at = now + datetime.timedelta(minutes=settings.VERIFICATION_CODE_TTL_MINUTES)
    user.password_reset_sent_at = now
    try:
        send_password_reset_email(user.email, code)
    except InvalidRecipientError:
        raise
    except Exception:
        logger.error("could not send password reset email to %s, code stored for retry", user.email)


def check_reset_code(user: User, submitted_code: str) -> bool:
    if not user.password_reset_code or not user.password_reset_code_expires_at:
        return False
    expires_at = user.password_reset_code_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if datetime.datetime.now(datetime.timezone.utc) > expires_at:
        return False
    return secrets.compare_digest(submitted_code.strip(), user.password_reset_code)


def clear_reset_code(user: User) -> None:
    user.password_reset_code = None
    user.password_reset_code_expires_at = None


def reset_resend_cooldown_remaining(user: User) -> int:
    if not user.password_reset_sent_at:
        return 0
    sent_at = user.password_reset_sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=datetime.timezone.utc)
    elapsed = (datetime.datetime.now(datetime.timezone.utc) - sent_at).total_seconds()
    remaining = settings.VERIFICATION_RESEND_COOLDOWN_SECONDS - elapsed
    return max(0, int(remaining))
