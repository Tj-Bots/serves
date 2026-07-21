from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    get_current_user,
    hash_password,
    is_allowed_email_domain,
    logout_user,
    password_strength_error,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import User
from app.services import deploy as deploy_service
from app.services.email import InvalidRecipientError
from app.services.verification import generate_and_send_code
from app.web_utils import flash, render

router = APIRouter()


@router.get("/account")
def account_page(request: Request, user: User = Depends(get_current_user)):
    return render(request, "account.html", user=user)


@router.post("/account/name")
def change_name(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    first_name = first_name.strip()
    last_name = last_name.strip()
    if not first_name or not last_name:
        flash(request, "auth.flash.name_required", "error")
        return RedirectResponse("/account", status_code=303)

    user.first_name = first_name
    user.last_name = last_name
    db.commit()
    flash(request, "account.flash.name_updated", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        flash(request, "account.flash.wrong_password", "error")
        return RedirectResponse("/account", status_code=303)

    password_error = password_strength_error(new_password)
    if password_error:
        flash(request, password_error, "error")
        return RedirectResponse("/account", status_code=303)

    if new_password != new_password_confirm:
        flash(request, "auth.flash.passwords_mismatch", "error")
        return RedirectResponse("/account", status_code=303)

    user.password_hash = hash_password(new_password)
    db.commit()
    flash(request, "account.flash.password_updated", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/email")
def change_email(
    request: Request,
    current_password: str = Form(...),
    new_email: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        flash(request, "account.flash.wrong_password", "error")
        return RedirectResponse("/account", status_code=303)

    new_email = new_email.strip().lower()
    if not is_allowed_email_domain(new_email):
        flash(request, "auth.flash.email_domain_not_allowed", "error")
        return RedirectResponse("/account", status_code=303)

    if db.query(User).filter(User.email == new_email, User.id != user.id).first():
        flash(request, "auth.flash.email_taken", "error")
        return RedirectResponse("/account", status_code=303)

    old_email = user.email
    user.email = new_email
    if settings.REQUIRE_EMAIL_VERIFICATION:
        user.is_verified = False
        try:
            generate_and_send_code(user)
        except InvalidRecipientError:
            user.email = old_email
            user.is_verified = True
            flash(request, "auth.flash.email_invalid", "error")
            return RedirectResponse("/account", status_code=303)
        db.commit()
        flash(request, "account.flash.email_updated_verify", "success")
        return RedirectResponse("/verify-email", status_code=303)

    db.commit()
    flash(request, "account.flash.email_updated", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/delete")
def delete_account(
    request: Request,
    current_password: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        flash(request, "account.flash.wrong_password", "error")
        return RedirectResponse("/account", status_code=303)

    for app in list(user.apps):
        deploy_service.teardown_app(app)

    db.delete(user)
    db.commit()

    flash(request, "account.flash.account_deleted", "success")
    logout_user(request)
    return RedirectResponse("/login", status_code=303)
