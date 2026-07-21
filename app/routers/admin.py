"""
פאנל ניהול פנימי לבעלי המערכת (לא למשתמשי הקצה). נטען תחת נתיב סודי
(settings.ADMIN_PATH) עם התחברות נפרדת משלו - לא קשור בכלל למערכת
ההתחברות של המשתמשים הרגילים. אם ADMIN_USERNAME/ADMIN_PASSWORD לא
מוגדרים ב-.env, כל הנתיבים כאן מחזירים 404 (הפאנל כבוי).
"""

import hmac

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.auth import AuthRedirect
from app.config import settings
from app.database import get_db
from app.models import AppStatus, BotApp, User
from app.services import deploy as deploy_service
from app.services import rate_limit
from app.web_utils import flash, render

router = APIRouter()

SESSION_KEY = "is_admin"


def _admin_enabled() -> bool:
    return bool(settings.ADMIN_USERNAME and settings.ADMIN_PASSWORD)


def _admin_url(path: str = "") -> str:
    return f"/{settings.ADMIN_PATH}{path}"


def _guard_enabled() -> None:
    if not _admin_enabled():
        raise HTTPException(status_code=404)


def require_admin(request: Request) -> None:
    _guard_enabled()
    if not request.session.get(SESSION_KEY):
        raise AuthRedirect(_admin_url("/login"))


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login")
def login_form(request: Request):
    _guard_enabled()
    if request.session.get(SESSION_KEY):
        return RedirectResponse(_admin_url(""), status_code=303)
    return render(request, "admin/login.html", admin_url=_admin_url)


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    _guard_enabled()
    ip = _client_ip(request)
    if not rate_limit.check(f"admin_login:{ip}", 5, 300):
        flash(request, "admin.flash.rate_limited", "error")
        return render(request, "admin/login.html", status_code=429, admin_url=_admin_url)

    ok = hmac.compare_digest(username, settings.ADMIN_USERNAME) and hmac.compare_digest(
        password, settings.ADMIN_PASSWORD
    )
    if not ok:
        flash(request, "admin.flash.bad_credentials", "error")
        return render(request, "admin/login.html", status_code=400, admin_url=_admin_url)

    request.session[SESSION_KEY] = True
    return RedirectResponse(_admin_url(""), status_code=303)


@router.get("/logout")
def logout(request: Request):
    _guard_enabled()
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(_admin_url("/login"), status_code=303)


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    total_users = db.query(func.count(User.id)).scalar()
    blocked_users = db.query(func.count(User.id)).filter(User.is_blocked.is_(True)).scalar()
    total_apps = db.query(func.count(BotApp.id)).scalar()
    running_apps = db.query(func.count(BotApp.id)).filter(BotApp.status == AppStatus.RUNNING).scalar()
    plan_counts = dict(db.query(User.plan, func.count(User.id)).group_by(User.plan).all())
    return render(
        request, "admin/dashboard.html", admin_url=_admin_url,
        total_users=total_users, blocked_users=blocked_users,
        total_apps=total_apps, running_apps=running_apps, plan_counts=plan_counts,
    )


@router.get("/users")
def users_list(request: Request, q: str = "", db: Session = Depends(get_db), _: None = Depends(require_admin)):
    query = db.query(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.email.ilike(like), User.first_name.ilike(like), User.last_name.ilike(like)))
    users = query.limit(300).all()
    return render(request, "admin/users.html", admin_url=_admin_url, users=users, q=q)


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    apps = db.query(BotApp).filter(BotApp.user_id == user_id).order_by(BotApp.created_at.desc()).all()
    return render(request, "admin/user_detail.html", admin_url=_admin_url, target=target, apps=apps)


@router.post("/users/{user_id}/block")
def block_user(request: Request, user_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    target.is_blocked = True
    db.commit()
    flash(request, "admin.flash.user_blocked", "success")
    return RedirectResponse(_admin_url(f"/users/{user_id}"), status_code=303)


@router.post("/users/{user_id}/unblock")
def unblock_user(request: Request, user_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    target.is_blocked = False
    db.commit()
    flash(request, "admin.flash.user_unblocked", "success")
    return RedirectResponse(_admin_url(f"/users/{user_id}"), status_code=303)


@router.post("/users/{user_id}/warn")
def warn_user(
    request: Request, user_id: int, message: str = Form(...),
    db: Session = Depends(get_db), _: None = Depends(require_admin),
):
    import datetime

    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    message = message.strip()
    target.warning_message = message or None
    target.warning_at = datetime.datetime.now(datetime.timezone.utc) if message else None
    db.commit()
    flash(request, "admin.flash.user_warned" if message else "admin.flash.warning_cleared", "success")
    return RedirectResponse(_admin_url(f"/users/{user_id}"), status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    for app in list(target.apps):
        deploy_service.teardown_app(app)
    db.delete(target)
    db.commit()
    flash(request, "admin.flash.user_deleted", "success")
    return RedirectResponse(_admin_url("/users"), status_code=303)


@router.get("/apps")
def apps_list(request: Request, q: str = "", db: Session = Depends(get_db), _: None = Depends(require_admin)):
    query = db.query(BotApp).join(User).order_by(BotApp.created_at.desc())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(BotApp.name.ilike(like), User.email.ilike(like)))
    apps = query.limit(300).all()
    return render(request, "admin/apps.html", admin_url=_admin_url, apps=apps, q=q)


@router.post("/apps/{app_id}/suspend")
def suspend_app(
    request: Request, app_id: int, reason: str = Form(""),
    db: Session = Depends(get_db), _: None = Depends(require_admin),
):
    app_row = db.get(BotApp, app_id)
    if not app_row:
        raise HTTPException(status_code=404)
    deploy_service.stop_app(app_row)
    app_row.admin_suspended = True
    app_row.admin_suspend_reason = reason.strip() or None
    db.commit()
    flash(request, "admin.flash.app_suspended", "success")
    return RedirectResponse(_admin_url("/apps"), status_code=303)


@router.post("/apps/{app_id}/unsuspend")
def unsuspend_app(request: Request, app_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    app_row = db.get(BotApp, app_id)
    if not app_row:
        raise HTTPException(status_code=404)
    app_row.admin_suspended = False
    app_row.admin_suspend_reason = None
    db.commit()
    flash(request, "admin.flash.app_unsuspended", "success")
    return RedirectResponse(_admin_url("/apps"), status_code=303)


@router.post("/apps/{app_id}/delete")
def delete_app(request: Request, app_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    app_row = db.get(BotApp, app_id)
    if not app_row:
        raise HTTPException(status_code=404)
    deploy_service.teardown_app(app_row)
    db.delete(app_row)
    db.commit()
    flash(request, "admin.flash.app_deleted", "success")
    return RedirectResponse(_admin_url("/apps"), status_code=303)
