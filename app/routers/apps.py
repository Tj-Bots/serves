import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_verified_user
from app.config import settings
from app.database import get_db
from app.models import AppStatus, BotApp, User
from app.security_policy import PolicyViolation, check_run_command
from app.services import deploy as deploy_service
from app.services import log_broadcaster
from app.slugs import slugify
from app.web_utils import flash, render

router = APIRouter()


def _get_owned_app(db: Session, user: User, app_id: int) -> BotApp:
    app = db.get(BotApp, app_id)
    if not app or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


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
    return render(
        request,
        "dashboard.html",
        user=user,
        apps=apps,
        max_apps=settings.FREE_MAX_APPS,
        memory_mb=settings.FREE_MEMORY_MB,
        cpu_cores=settings.FREE_CPU_CORES,
        disk_mb=settings.FREE_DISK_MB,
        can_create=len(apps) < settings.FREE_MAX_APPS,
    )


@router.get("/apps/new")
def new_app_form(request: Request, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    count = db.query(BotApp).filter(BotApp.user_id == user.id).count()
    if count >= settings.FREE_MAX_APPS:
        flash(request, "apps.flash.limit_with_hint", "error", max_apps=settings.FREE_MAX_APPS)
        return RedirectResponse("/dashboard", status_code=303)
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
    count = db.query(BotApp).filter(BotApp.user_id == user.id).count()
    if count >= settings.FREE_MAX_APPS:
        flash(request, "apps.flash.limit", "error", max_apps=settings.FREE_MAX_APPS)
        return RedirectResponse("/dashboard", status_code=303)

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

    app.slug = f"{slugify(name)}-{app.id}"
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
    app_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
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
    app_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
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
