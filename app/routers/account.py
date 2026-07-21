from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    get_current_user,
    hash_password,
    logout_user,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import User
from app.services import deploy as deploy_service
from app.services.verification import generate_and_send_code
from app.web_utils import flash, render

router = APIRouter()


@router.get("/account")
def account_page(request: Request, user: User = Depends(get_current_user)):
    return render(request, "account.html", user=user)


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

    if len(new_password) < 8:
        flash(request, "auth.flash.password_too_short", "error")
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
    if db.query(User).filter(User.email == new_email, User.id != user.id).first():
        flash(request, "auth.flash.email_taken", "error")
        return RedirectResponse("/account", status_code=303)

    user.email = new_email
    if settings.REQUIRE_EMAIL_VERIFICATION:
        user.is_verified = False
        generate_and_send_code(user)
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
