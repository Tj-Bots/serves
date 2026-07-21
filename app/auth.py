import bcrypt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.web_utils import flash

SESSION_KEY = "user_id"


class AuthRedirect(Exception):
    """Raised by get_current_user when there is no logged-in session."""

    def __init__(self, to: str = "/login"):
        self.to = to


def hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


ALLOWED_EMAIL_DOMAINS = {"gmail.com"}


def is_allowed_email_domain(email: str) -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in ALLOWED_EMAIL_DOMAINS


def password_strength_error(password: str) -> str | None:
    """מחזיר מפתח תרגום לשגיאה אם הסיסמא חלשה מדי, אחרת None."""
    if len(password) < 8:
        return "auth.flash.password_too_short"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "auth.flash.password_too_weak"
    return None


def verify_password(raw_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def login_user(request: Request, user: User) -> None:
    request.session[SESSION_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    return db.get(User, user_id)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_optional_user(request, db)
    if not user:
        raise AuthRedirect("/login")
    if user.is_blocked:
        logout_user(request)
        flash(request, "auth.flash.account_blocked", "error")
        raise AuthRedirect("/login")
    return user


def get_current_verified_user(user: User = Depends(get_current_user)) -> User:
    if settings.REQUIRE_EMAIL_VERIFICATION and not user.is_verified:
        raise AuthRedirect("/verify-email")
    return user
