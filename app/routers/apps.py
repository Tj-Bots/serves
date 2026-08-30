import asyncio
import re
import secrets

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
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
from app.slugs import RESERVED_SLUGS, slugify
from app.trial import is_trial_expired, trial_days_remaining
from app.web_utils import flash, render

router = APIRouter()

# רק אותיות אנגליות וספרות, לא מתחיל בספרה - כדי שהשם יהיה שמיש בתור
# חלק מה-slug הציבורי (/open/<name>-<random>) בלי צורך בתעתיק/סינון.
APP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def _get_owned_app(db: Session, user: User, app_id: int) -> BotApp:
    app = db.get(BotApp, app_id)
    if not app or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _plan(user: User) -> dict:
    return PLANS.get(user.plan, PLANS["free"])


def _max_apps(user: User) -> int:
    return _plan(user)["max_apps"]


def _is_top_plan(user: User) -> bool:
    return _max_apps(user) >= max(p["max_apps"] for p in PLANS.values())


def _limit_reached_response(request: Request, user: User, max_apps: int) -> RedirectResponse:
    """כשמגיעים למגבלת האפליקציות: אם יש תוכנית בתשלום זמינה ומוגדרת
    ואפשר עוד לשדרג, שולחים לעמוד השדרוג במקום סתם להחזיר לדשבורד עם
    הודעת שגיאה."""
    if settings.PAYMENT_BOT_USERNAME and not _is_top_plan(user):
        flash(request, "apps.flash.limit_upgrade_hint", "error", max_apps=max_apps)
        return RedirectResponse("/billing", status_code=303)
    flash(request, "apps.flash.limit_with_hint", "error", max_apps=max_apps)
    return RedirectResponse("/dashboard", status_code=303)


def _trial_expired_response(request: Request, user: User, redirect_to: str) -> RedirectResponse:
    """כשתקופת הניסיון החינמית נגמרה: אם יש תוכנית בתשלום זמינה, שולחים
    לעמוד השדרוג - אחרת חזרה למקור עם הודעה."""
    if settings.PAYMENT_BOT_USERNAME:
        flash(request, "apps.flash.trial_expired_upgrade", "error")
        return RedirectResponse("/billing", status_code=303)
    flash(request, "apps.flash.trial_expired", "error")
    return RedirectResponse(redirect_to, status_code=303)


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
    plan = _plan(user)
    max_apps = plan["max_apps"]

    trial_expired = is_trial_expired(user)
    if trial_expired:
        # אכיפה עצלה: כל עוד המשתמש בתוכנית חינמית שפג תוקפה, אין טעם
        # שהאפליקציות שלו ימשיכו לצרוך משאבים - עוצרים אותן בכל טעינת
        # דשבורד (עצירה של אפליקציה שכבר עצורה לא עושה כלום, אז זה זול).
        for app in apps:
            if app.status == AppStatus.RUNNING:
                deploy_service.stop_app(app)

    return render(
        request,
        "dashboard.html",
        user=user,
        apps=apps,
        max_apps=max_apps,
        memory_mb=plan["memory_mb"],
        cpu_cores=plan["cpu_cores"],
        disk_mb=plan["disk_mb"],
        bandwidth_mbps=plan["bandwidth_mbps"],
        can_create=len(apps) < max_apps and not trial_expired,
        payments_enabled=bool(settings.PAYMENT_BOT_USERNAME),
        can_upgrade=bool(settings.PAYMENT_BOT_USERNAME) and not _is_top_plan(user),
        trial_expired=trial_expired,
        trial_days_left=trial_days_remaining(user),
    )


@router.get("/apps/new")
def new_app_form(request: Request, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    if is_trial_expired(user):
        return _trial_expired_response(request, user, "/dashboard")
    max_apps = _max_apps(user)
    count = db.query(BotApp).filter(BotApp.user_id == user.id).count()
    if count >= max_apps:
        return _limit_reached_response(request, user, max_apps)
    return render(request, "new_app.html", user=user, max_zip_mb=settings.MAX_ZIP_UPLOAD_MB)


async def _save_uploaded_zip(app_id: int, upload: UploadFile) -> None:
    """שומר zip שהועלה ב-app_root_dir(app_id)/source.zip, בזרימה (בלי לטעון
    הכל לזיכרון) ותוך אכיפת MAX_ZIP_UPLOAD_MB - זה קורה לפני שה-volume
    המוגבל-דיסק לפי תוכנית נוצר (רק ב-deploy()), אז זו הגנת הגודל היחידה כאן."""
    dest = deploy_service.source_zip_path(app_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.MAX_ZIP_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise RuntimeError("zip file too large")
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


_GITHUB_REPO_RE = re.compile(r"^https?://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


@router.get("/apps/branches")
async def list_branches(repo_url: str, user: User = Depends(get_current_verified_user)):
    """מחזיר את רשימת הענפים של ריפו ב-GitHub (ל-select בטופס יצירת
    אפליקציה) - עובד רק לריפואים ציבוריים ב-github.com (ה-API הציבורי,
    בלי אימות). לריפואים מ-hosts אחרים מחזיר רשימה ריקה - אפשר עדיין
    להקליד שם ענף ידנית בטופס."""
    match = _GITHUB_REPO_RE.match((repo_url or "").strip())
    if not match:
        return JSONResponse({"branches": []})
    owner, repo = match.group(1), match.group(2)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/branches",
                params={"per_page": 100},
                headers={"Accept": "application/vnd.github+json"},
            )
        if resp.status_code != 200:
            return JSONResponse({"branches": []})
        names = [b["name"] for b in resp.json() if isinstance(b, dict) and b.get("name")]
        repo_info = await _fetch_default_branch(owner, repo)
    except Exception:
        return JSONResponse({"branches": []})
    return JSONResponse({"branches": names, "default_branch": repo_info})


async def _fetch_default_branch(owner: str, repo: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
        if resp.status_code == 200:
            return resp.json().get("default_branch")
    except Exception:
        pass
    return None


@router.post("/apps/new")
async def create_app(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    source_type: str = Form("git"),
    repo_url: str = Form(""),
    branch: str = Form(""),
    zip_file: UploadFile | None = File(None),
    use_dockerfile: bool = Form(False),
    requirements_file: str = Form("requirements.txt"),
    run_command: str = Form(""),
    env_text: str = Form(""),
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    if is_trial_expired(user):
        return _trial_expired_response(request, user, "/apps/new")

    max_apps = _max_apps(user)
    count = db.query(BotApp).filter(BotApp.user_id == user.id).count()
    if count >= max_apps:
        return _limit_reached_response(request, user, max_apps)

    if not _check_deploy_rate_limit(request, user):
        return RedirectResponse("/apps/new", status_code=303)

    name = name.strip()
    source_type = "zip" if source_type == "zip" else "git"
    repo_url = repo_url.strip()
    branch = branch.strip()
    requirements_file = (requirements_file or "requirements.txt").strip() or "requirements.txt"
    run_command = run_command.strip()

    if not name or (not use_dockerfile and not run_command):
        flash(request, "apps.flash.fill_all_fields", "error")
        return RedirectResponse("/apps/new", status_code=303)

    if not APP_NAME_RE.match(name):
        flash(request, "apps.flash.invalid_name", "error")
        return RedirectResponse("/apps/new", status_code=303)

    if source_type == "git":
        if not repo_url:
            flash(request, "apps.flash.fill_all_fields", "error")
            return RedirectResponse("/apps/new", status_code=303)
    else:
        if not zip_file or not zip_file.filename:
            flash(request, "apps.flash.zip_required", "error")
            return RedirectResponse("/apps/new", status_code=303)

    if db.query(BotApp).filter(func.lower(BotApp.name) == name.lower()).first():
        flash(request, "apps.flash.name_taken", "error")
        return RedirectResponse("/apps/new", status_code=303)

    if not use_dockerfile:
        try:
            check_run_command(run_command)
        except PolicyViolation as exc:
            flash(request, exc.message, "error")
            return RedirectResponse("/apps/new", status_code=303)

    app = BotApp(
        user_id=user.id,
        name=name,
        source_type=source_type,
        repo_url=repo_url if source_type == "git" else "",
        branch=(branch if source_type == "git" else None) or None,
        use_dockerfile=use_dockerfile,
        requirements_file=requirements_file,
        run_command="" if use_dockerfile else run_command,
        env_vars=_parse_env_text(env_text),
        status=AppStatus.PENDING,
    )
    db.add(app)
    db.commit()
    db.refresh(app)

    # ה-slug כולל תמיד סיומת רנדומלית (לא מזהה עוקב כמו app.id) כדי שכתובות
    # אפליקציות לא יהיו ניתנות לניחוש/סריקה ע"י מישהו שמנחש שמות אפליקציות.
    base_slug = slugify(name)
    for _ in range(5):
        candidate = f"{base_slug}-{secrets.token_hex(3)}"
        if candidate not in RESERVED_SLUGS and not db.query(BotApp).filter(BotApp.slug == candidate).first():
            app.slug = candidate
            break
    else:
        app.slug = f"{base_slug}-{secrets.token_hex(6)}"
    db.commit()

    if source_type == "zip":
        try:
            await _save_uploaded_zip(app.id, zip_file)
        except Exception:
            flash(request, "apps.flash.zip_too_large", "error")
            return RedirectResponse(f"/apps/{app.id}", status_code=303)

    loop = asyncio.get_running_loop()
    background_tasks.add_task(deploy_service.deploy, app.id, loop)

    return RedirectResponse(f"/apps/{app.id}", status_code=303)


def _app_public_url(app: BotApp) -> str:
    # תמיד נתיב תחת הדומיין הראשי (/open/<slug>) - לא סאב-דומיין, גם אם
    # APPS_BASE_DOMAIN מוגדר (הוא עדיין קיים כתשתית, פשוט לא בשימוש כברירת
    # מחדל יותר - עדיף כתובת אחת פשוטה שתמיד עובדת בלי תלות ב-DNS/SSL חיצוני).
    return f"/open/{app.slug or app.id}"


@router.get("/apps/{app_id}")
def app_detail(request: Request, app_id: int, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    app = _get_owned_app(db, user, app_id)
    log_tail = log_broadcaster.read_tail(app_id)
    env_text = "\n".join(f"{k}={v}" for k, v in (app.env_vars or {}).items())
    return render(
        request, "app_detail.html", user=user, app=app, log_tail=log_tail, env_text=env_text,
        app_url=_app_public_url(app),
    )


def _check_not_suspended(request: Request, app: BotApp) -> bool:
    """True אם מותר להמשיך. אפליקציה שהושעתה ע"י מנהל לא ניתנת להפעלה
    מחדש ע"י המשתמש - רק מנהל יכול לבטל את ההשעיה."""
    if app.admin_suspended:
        flash(request, "apps.flash.admin_suspended", "error")
        return False
    return True


@router.post("/apps/{app_id}/zip")
async def update_zip(
    request: Request,
    app_id: int,
    zip_file: UploadFile = File(...),
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
    if app.source_type != "zip":
        raise HTTPException(status_code=400, detail="This application is not zip-based")
    if not zip_file.filename:
        flash(request, "apps.flash.zip_required", "error")
        return RedirectResponse(f"/apps/{app_id}", status_code=303)
    try:
        await _save_uploaded_zip(app_id, zip_file)
    except Exception:
        flash(request, "apps.flash.zip_too_large", "error")
        return RedirectResponse(f"/apps/{app_id}", status_code=303)
    flash(request, "apps.flash.zip_uploaded", "success")
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/apps/{app_id}/redeploy")
async def redeploy(
    request: Request,
    app_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    app = _get_owned_app(db, user, app_id)
    if not _check_not_suspended(request, app):
        return RedirectResponse(f"/apps/{app_id}", status_code=303)
    if is_trial_expired(user):
        return _trial_expired_response(request, user, f"/apps/{app_id}")
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
    if not _check_not_suspended(request, app):
        return RedirectResponse(f"/apps/{app_id}", status_code=303)
    if is_trial_expired(user):
        return _trial_expired_response(request, user, f"/apps/{app_id}")
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
