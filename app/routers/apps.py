import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_verified_user
from app.config import PLANS, settings
from app.database import get_db
from app.models import AppStatus, BotApp, User
from app.security_policy import PolicyViolation, check_run_command
from app.services import deploy as deploy_service
from app.services import log_broadcaster
from app.services import rate_limit
from app.slugs import slugify
from app.web_utils import flash, render

router = APIRouter()


def _get_owned_app(db: Session, user: User, app_id: int) -> BotApp:
    app = db.get(BotApp, app_id)
    if not app or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _max_apps(user: User) -> int:
    return PLANS.get(user.plan, PLANS["free"])["max_apps"]


def _limit_reached_response(request: Request, user: User, max_apps: int) -> RedirectResponse:
    """כשמגיעים למגבלת האפליקציות: אם יש תוכנית בתשלום זמינה ומוגדרת,
    שולחים לעמוד השדרוג במקום סתם להחזיר לדשבורד עם הודעת שגיאה."""
    if settings.PAYMENT_BOT_USERNAME and user.plan != "pro":
        flash(request, "apps.flash.limit_upgrade_hint", "error", max_apps=max_apps)
        return RedirectResponse("/billing", status_code=303)
    flash(request, "apps.flash.limit_with_hint", "error", max_apps=max_apps)
    return RedirectResponse("/dashboard", status_code=303)


def _check_deploy_rate_limit(request: Request, user: User) -> bool:
    """True אם מותר להמשיך. אחרת שם flash ומחזיר False - הקורא צריך
    להחזיר redirect בעצמו (כדי לשמור על הנתיב הנכון)."""
    if not rate_limit.check(f"deploy:{user.id}", settings.DEPLOY_ACTION_MAX, settings.DEPLOY_ACTION_WINDOW_SECONDS):
        flash(request, "apps.flash.deploy_rate_limited", "error")
        return False
    return True


def _parse_env_text(env_text: str) -> dict:
    env_vars = {}
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            env_vars[key] = value.strip()
    return env_vars


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    from app.auth import get_optional_user

    if get_optional_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    apps = db.query(BotApp).filter(BotApp.user_id == user.id).order_by(BotApp.created_at.desc()).all()
    max_apps = _max_apps(user)
    return render(
        request,
        "dashboard.html",
        user=user,
        apps=apps,
        max_apps=max_apps,
        memory_mb=settings.FREE_MEMORY_MB,
        cpu_cores=settings.FREE_CPU_CORES,
        disk_mb=settings.FREE_DISK_MB,
        can_create=len(apps) < max_apps,
        payments_enabled=bool(settings.PAYMENT_BOT_USERNAME),
    )


@router.get("/apps/new")
def new_app_form(request: Request, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    max_apps = _max_apps(user)
    count = db.query(BotApp).filter(BotApp.user_id == user.id).count()
    if count >= max_apps:
        return _limit_reached_response(request, user, max_apps)
    return render(request, "new_app.html", user=user)


@router.post("/apps/new")
async def create_app(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    repo_url: str = Form(...),
    requirements_file: str = Form("requirements.txt"),
    run_command: str = Form(...),
    env_text: str = Form(""),
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    max_apps = _max_apps(user)
    count = db.query(BotApp).filter(BotApp.user_id == user.id).count()
    if count >= max_apps:
        return _limit_reached_response(request, user, max_apps)

    if not _check_deploy_rate_limit(request, user):
        return RedirectResponse("/apps/new", status_code=303)

    name = name.strip()
    repo_url = repo_url.strip()
    requirements_file = (requirements_file or "requirements.txt").strip() or "requirements.txt"
    run_command = run_command.strip()

    if not name or not repo_url or not run_command:
        flash(request, "apps.flash.fill_all_fields", "error")
        return RedirectResponse("/apps/new", status_code=303)

    if db.query(BotApp).filter(func.lower(BotApp.name) == name.lower()).first():
        flash(request, "apps.flash.name_taken", "error")
        return RedirectResponse("/apps/new", status_code=303)

    try:
        check_run_command(run_command)
    except PolicyViolation as exc:
        flash(request, exc.message, "error")
        return RedirectResponse("/apps/new", status_code=303)

    app = BotApp(
        user_id=user.id,
        name=name,
        repo_url=repo_url,
        requirements_file=requirements_file,
        run_command=run_command,
        env_vars=_parse_env_text(env_text),
        status=AppStatus.PENDING,
    )
    db.add(app)
    db.commit()
    db.refresh(app)

    base_slug = slugify(name)
    if db.query(BotApp).filter(BotApp.slug == base_slug).first():
        app.slug = f"{base_slug}-{app.id}"
    else:
        app.slug = base_slug
    db.commit()

    loop = asyncio.get_running_loop()
    background_tasks.add_task(deploy_service.deploy, app.id, loop)

    return RedirectResponse(f"/apps/{app.id}", status_code=303)


@router.get("/apps/{app_id}")
def app_detail(request: Request, app_id: int, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    app = _get_owned_app(db, user, app_id)
    log_tail = log_broadcaster.read_tail(app_id)
    env_text = "\n".join(f"{k}={v}" for k, v in (app.env_vars or {}).items())
    return render(request, "app_detail.html", user=user, app=app, log_tail=log_tail, env_text=env_text)


@router.post("/apps/{app_id}/redeploy")
async def redeploy(
    request: Request,
    app_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
    if not _check_deploy_rate_limit(request, user):
        return RedirectResponse(f"/apps/{app_id}", status_code=303)
    loop = asyncio.get_running_loop()
    background_tasks.add_task(deploy_service.deploy, app.id, loop)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/apps/{app_id}/stop")
def stop(app_id: int, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    app = _get_owned_app(db, user, app_id)
    deploy_service.stop_app(app)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/apps/{app_id}/start")
async def start(
    request: Request,
    app_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
    if not _check_deploy_rate_limit(request, user):
        return RedirectResponse(f"/apps/{app_id}", status_code=303)
    loop = asyncio.get_running_loop()
    background_tasks.add_task(deploy_service.start_app, app.id, loop)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/apps/{app_id}/delete")
def delete(app_id: int, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    app = _get_owned_app(db, user, app_id)
    deploy_service.teardown_app(app)
    db.delete(app)
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/apps/{app_id}/env")
def update_env(
    request: Request,
    app_id: int,
    env_text: str = Form(""),
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
    app.env_vars = _parse_env_text(env_text)
    db.commit()
    flash(request, "apps.flash.env_saved", "success")
    return RedirectResponse(f"/apps/{app_id}", status_code=303)
