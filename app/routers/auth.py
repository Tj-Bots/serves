import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_optional_user, hash_password, login_user, logout_user, verify_password
from app.database import get_db
from app.models import User
from app.services.verification import check_code, generate_and_send_code, mark_verified, resend_cooldown_remaining
from app.web_utils import flash, render

router = APIRouter()


@router.get("/signup")
def signup_form(request: Request, db: Session = Depends(get_db)):
    if get_optional_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "signup.html")


@router.post("/signup")
def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    accept_terms: bool = Form(False),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()

    if not accept_terms:
        flash(request, "יש לאשר את התקנון כדי להירשם.", "error")
        return render(request, "signup.html", status_code=400, email=email)

    if len(password) < 8:
        flash(request, "הסיסמא חייבת להכיל לפחות 8 תווים.", "error")
        return render(request, "signup.html", status_code=400, email=email)

    if password != password_confirm:
        flash(request, "הסיסמאות אינן תואמות.", "error")
        return render(request, "signup.html", status_code=400, email=email)

    if db.query(User).filter(User.email == email).first():
        flash(request, "כבר קיים משתמש עם המייל הזה.", "error")
        return render(request, "signup.html", status_code=400, email=email)

    user = User(
        email=email,
        password_hash=hash_password(password),
        accepted_terms_at=datetime.datetime.now(datetime.timezone.utc),
    )
    generate_and_send_code(user)
    db.add(user)
    db.commit()
    db.refresh(user)

    login_user(request, user)
    flash(request, f"נשלח קוד אימות בן 6 ספרות לכתובת {email}.", "success")
    return RedirectResponse("/verify-email", status_code=303)


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
        flash(request, "מייל או סיסמא שגויים.", "error")
        return render(request, "login.html", status_code=400, email=email)

    login_user(request, user)
    if not user.is_verified:
        return RedirectResponse("/verify-email", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/verify-email")
def verify_email_form(request: Request, user: User = Depends(get_current_user)):
    if user.is_verified:
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "verify_email.html", user=user)


@router.post("/verify-email")
def verify_email_submit(
    request: Request,
    code: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.is_verified:
        return RedirectResponse("/dashboard", status_code=303)

    if not check_code(user, code):
        flash(request, "קוד שגוי או שפג תוקפו. אפשר לבקש קוד חדש.", "error")
        return render(request, "verify_email.html", status_code=400, user=user)

    mark_verified(user)
    db.commit()
    flash(request, "האימייל אומת בהצלחה!", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/verify-email/resend")
def verify_email_resend(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.is_verified:
        return RedirectResponse("/dashboard", status_code=303)

    remaining = resend_cooldown_remaining(user)
    if remaining > 0:
        flash(request, f"אפשר לבקש קוד חדש בעוד {remaining} שניות.", "error")
        return RedirectResponse("/verify-email", status_code=303)

    generate_and_send_code(user)
    db.commit()
    flash(request, "קוד חדש נשלח למייל.", "success")
    return RedirectResponse("/verify-email", status_code=303)
