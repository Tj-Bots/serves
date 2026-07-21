import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    get_current_user,
    get_optional_user,
    hash_password,
    is_allowed_email_domain,
    login_user,
    logout_user,
    password_strength_error,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import User
from app.services import rate_limit
from app.services.email import InvalidRecipientError
from app.services.password_reset import (
    check_reset_code,
    clear_reset_code,
    generate_and_send_reset_code,
    reset_resend_cooldown_remaining,
)
from app.services.verification import check_code, generate_and_send_code, mark_verified, resend_cooldown_remaining
from app.web_utils import flash, render

router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/signup")
def signup_form(request: Request, db: Session = Depends(get_db)):
    if get_optional_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "signup.html")


@router.post("/signup")
def signup_submit(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    accept_terms: bool = Form(False),
    db: Session = Depends(get_db),
):
    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()
    form_kwargs = dict(email=email, first_name=first_name, last_name=last_name)

    if not accept_terms:
        flash(request, "auth.flash.must_accept_terms", "error")
        return render(request, "signup.html", status_code=400, **form_kwargs)

    if not first_name or not last_name:
        flash(request, "auth.flash.name_required", "error")
        return render(request, "signup.html", status_code=400, **form_kwargs)

    if not is_allowed_email_domain(email):
        flash(request, "auth.flash.email_domain_not_allowed", "error")
        return render(request, "signup.html", status_code=400, **form_kwargs)

    password_error = password_strength_error(password)
    if password_error:
        flash(request, password_error, "error")
        return render(request, "signup.html", status_code=400, **form_kwargs)

    if password != password_confirm:
        flash(request, "auth.flash.passwords_mismatch", "error")
        return render(request, "signup.html", status_code=400, **form_kwargs)

    if db.query(User).filter(User.email == email).first():
        flash(request, "auth.flash.email_taken", "error")
        return render(request, "signup.html", status_code=400, **form_kwargs)

    ip = _client_ip(request)
    if not rate_limit.check(f"signup:{ip}", settings.SIGNUP_IP_MAX_PER_DAY, 86400):
        flash(request, "auth.flash.signup_rate_limited", "error")
        return render(request, "signup.html", status_code=429, **form_kwargs)

    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        password_hash=hash_password(password),
        accepted_terms_at=datetime.datetime.now(datetime.timezone.utc),
        signup_ip=ip,
    )

    if settings.REQUIRE_EMAIL_VERIFICATION:
        try:
            generate_and_send_code(user)
        except InvalidRecipientError:
            flash(request, "auth.flash.email_invalid", "error")
            return render(request, "signup.html", status_code=400, **form_kwargs)
    else:
        user.is_verified = True

    db.add(user)
    db.commit()
    db.refresh(user)

    login_user(request, user)

    if settings.REQUIRE_EMAIL_VERIFICATION:
        flash(request, "auth.flash.code_sent", "success", email=email)
        return RedirectResponse("/verify-email", status_code=303)

    flash(request, "auth.flash.welcome", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_optional_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        flash(request, "auth.flash.bad_credentials", "error")
        return render(request, "login.html", status_code=400, email=email)

    login_user(request, user)
    if settings.REQUIRE_EMAIL_VERIFICATION and not user.is_verified:
        return RedirectResponse("/verify-email", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/verify-email")
def verify_email_form(request: Request, user: User = Depends(get_current_user)):
    if user.is_verified or not settings.REQUIRE_EMAIL_VERIFICATION:
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "verify_email.html", user=user)


@router.post("/verify-email")
def verify_email_submit(
    request: Request,
    code: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.is_verified or not settings.REQUIRE_EMAIL_VERIFICATION:
        return RedirectResponse("/dashboard", status_code=303)

    if not check_code(user, code):
        flash(request, "verify.flash.bad_code", "error")
        return render(request, "verify_email.html", status_code=400, user=user)

    mark_verified(user)
    db.commit()
    flash(request, "verify.flash.success", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/verify-email/resend")
def verify_email_resend(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.is_verified or not settings.REQUIRE_EMAIL_VERIFICATION:
        return RedirectResponse("/dashboard", status_code=303)

    remaining = resend_cooldown_remaining(user)
    if remaining > 0:
        flash(request, "verify.flash.cooldown", "error", seconds=remaining)
        return RedirectResponse("/verify-email", status_code=303)

    try:
        generate_and_send_code(user)
    except InvalidRecipientError:
        flash(request, "auth.flash.email_invalid", "error")
        return RedirectResponse("/verify-email", status_code=303)
    db.commit()
    flash(request, "verify.flash.resent", "success")
    return RedirectResponse("/verify-email", status_code=303)


@router.get("/forgot-password")
def forgot_password_form(request: Request, db: Session = Depends(get_db)):
    if get_optional_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "forgot_password.html")


@router.post("/forgot-password")
def forgot_password_submit(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()

    # תמיד אותה הודעה בלי קשר אם המייל קיים, כדי לא לחשוף אילו כתובות רשומות
    if user:
        try:
            generate_and_send_reset_code(user)
            db.commit()
        except InvalidRecipientError:
            pass
        request.session["reset_email"] = email

    flash(request, "auth.flash.reset_code_sent", "success")
    return RedirectResponse("/reset-password", status_code=303)


@router.get("/reset-password")
def reset_password_form(request: Request):
    if not request.session.get("reset_email"):
        return RedirectResponse("/forgot-password", status_code=303)
    return render(request, "reset_password.html")


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    code: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    email = request.session.get("reset_email")
    if not email:
        return RedirectResponse("/forgot-password", status_code=303)

    user = db.query(User).filter(User.email == email).first()
    if not user or not check_reset_code(user, code):
        flash(request, "auth.flash.reset_bad_code", "error")
        return render(request, "reset_password.html", status_code=400)

    password_error = password_strength_error(new_password)
    if password_error:
        flash(request, password_error, "error")
        return render(request, "reset_password.html", status_code=400)

    if new_password != new_password_confirm:
        flash(request, "auth.flash.passwords_mismatch", "error")
        return render(request, "reset_password.html", status_code=400)

    user.password_hash = hash_password(new_password)
    clear_reset_code(user)
    db.commit()
    request.session.pop("reset_email", None)

    flash(request, "auth.flash.reset_success", "success")
    return RedirectResponse("/login", status_code=303)


@router.post("/reset-password/resend")
def reset_password_resend(request: Request, db: Session = Depends(get_db)):
    email = request.session.get("reset_email")
    if not email:
        return RedirectResponse("/forgot-password", status_code=303)

    user = db.query(User).filter(User.email == email).first()
    if not user:
        return RedirectResponse("/forgot-password", status_code=303)

    remaining = reset_resend_cooldown_remaining(user)
    if remaining > 0:
        flash(request, "verify.flash.cooldown", "error", seconds=remaining)
        return RedirectResponse("/reset-password", status_code=303)

    try:
        generate_and_send_reset_code(user)
        db.commit()
    except InvalidRecipientError:
        pass
    flash(request, "verify.flash.resent", "success")
    return RedirectResponse("/reset-password", status_code=303)
