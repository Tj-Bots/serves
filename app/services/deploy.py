"""
שירות הפריסה: משכפל את הריפו, בודק מדיניות אבטחה, ומריץ את הבוט דרך
שכבת ה-runtime (Docker בפרודקשן). רץ כ-background task אחרי יצירת/עדכון
אפליקציה, כדי שהבקשה ה-HTTP תחזור מיד והלוגים יופיעו בזמן אמת ב-UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path

from app.config import PLANS, settings
from app.database import SessionLocal
from app.models import AppStatus, BotApp
from app.security_policy import PolicyViolation, check_requirements, check_run_command
from app.services import log_broadcaster
from app.services import telegram as telegram_service
from app.services.docker_manager import runtime


def _plan_resources(plan_name: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["free"])
    return {
        "memory_mb": plan["memory_mb"],
        "cpu_cores": plan["cpu_cores"],
        "disk_mb": plan["disk_mb"],
        "bandwidth_mbps": plan["bandwidth_mbps"],
    }

logger = logging.getLogger("serves.deploy")


def app_root_dir(app_id: int) -> Path:
    return settings.APPS_DIR / str(app_id)


def app_code_dir(app_id: int) -> Path:
    return app_root_dir(app_id) / "code"


def _disk_image_path(app_id: int) -> Path:
    return app_root_dir(app_id) / "disk.img"


def source_zip_path(app_id: int) -> Path:
    return app_root_dir(app_id) / "source.zip"


def _extract_zip_safely(zip_path: Path, dest_dir: Path) -> None:
    """מחלץ zip שהועלה ע"י המשתמש ל-dest_dir, עם הגנה מפני zip-slip
    (חברים בארכיון עם '..' או נתיב מוחלט שיכולים לכתוב מחוץ ל-dest_dir)."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError(f"zip file contains an unsafe path: {name}")
        zf.extractall(dest_dir)

    # אם כל התוכן ארוז בתיקיית-שורש יחידה (הפורמט הנפוץ של "Download ZIP"
    # מגיטהאב), מרימים את התוכן שלה החוצה כדי ש-run_command יעבוד מהנתיב הצפוי
    entries = list(dest_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        wrapper = entries[0]
        for item in wrapper.iterdir():
            shutil.move(str(item), str(dest_dir / item.name))
        wrapper.rmdir()


def _ensure_app_volume(app_id: int, disk_mb: int) -> None:
    """מוודא ש-/app רץ על מערכת קבצים בגודל קבוע (loop device), כדי
    לאכוף בפועל את מגבלת האחסון (disk_mb - לפי התוכנית של הבעלים, ראו
    _plan_resources) - ולא רק כתיקייה רגילה על דיסק המארח שאין לה תקרה.
    אידמפוטנטי: אם כבר mounted, לא עושה כלום; אם קובץ ה-image כבר קיים
    (למשל אחרי restart של השרת, או שהמשתמש שודרג לתוכנית עם דיסק גדול
    יותר בזמן שהאפליקציה כבר קיימת), רק מחבר אותו מחדש בלי לאבד נתונים -
    שינוי גודל ל-image קיים לא נתמך כרגע, צריך "פריסה מחדש" אחרי שדרוג
    כדי שמגבלת הדיסק החדשה תיכנס לתוקף בפועל."""
    if settings.DISABLE_DOCKER:
        app_code_dir(app_id).mkdir(parents=True, exist_ok=True)
        return

    mount_dir = app_code_dir(app_id)
    mount_dir.mkdir(parents=True, exist_ok=True)
    if os.path.ismount(mount_dir):
        return

    image_path = _disk_image_path(app_id)
    if not image_path.exists():
        image_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["truncate", "-s", f"{disk_mb}M", str(image_path)], check=True)
        subprocess.run(["mkfs.ext4", "-q", "-m", "0", "-F", str(image_path)], check=True)

    subprocess.run(["mount", "-o", "loop", str(image_path), str(mount_dir)], check=True)


def _clear_dir_contents(path: Path) -> None:
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def _release_app_volume(app_id: int) -> None:
    if settings.DISABLE_DOCKER:
        return
    mount_dir = app_code_dir(app_id)
    if os.path.ismount(mount_dir):
        subprocess.run(["umount", str(mount_dir)], check=False)


def _fix_ownership(code_dir: Path) -> None:
    """הקוד משוכפל ע"י תהליך הפלטפורמה (root), אבל רץ בקונטיינר כ-botuser
    (uid קבוע, ראו docker/base.Dockerfile) - בלי chown הוא לא יכול לכתוב
    אפילו ל-pip install --user שלו. לא רלוונטי ל-LocalProcessRuntime."""
    if settings.DISABLE_DOCKER:
        return
    subprocess.run(
        ["chown", "-R", f"{settings.SANDBOX_UID}:{settings.SANDBOX_GID}", str(code_dir)],
        check=False,
    )


def _set_telegram_username(app_id: int, username: str | None) -> None:
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id)
        if app:
            app.telegram_username = username
            db.commit()
    finally:
        db.close()


def _set_status(app_id: int, status: AppStatus, error: str | None = None, container_id: str | None = None) -> None:
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id)
        if not app:
            return
        app.status = status
        if error is not None:
            app.last_error = error[-2000:]
        if container_id is not None:
            app.container_id = container_id
        db.commit()
    finally:
        db.close()


def _emit(app_id: int, loop: asyncio.AbstractEventLoop, line: str) -> None:
    asyncio.run_coroutine_threadsafe(log_broadcaster.append_line(app_id, line), loop)


def _get_status(app_id: int) -> AppStatus | None:
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id)
        return app.status if app else None
    finally:
        db.close()


def _watch(app_id: int, handle: str, loop: asyncio.AbstractEventLoop) -> None:
    def on_line(line: str) -> None:
        _emit(app_id, loop, line)

    def on_exit(code: int) -> None:
        # אם הסטטוס כבר STOPPED זה אומר שהמשתמש עצר את זה במפורש
        # (stop_app קבע את זה כבר) - לא לדרוס בחזרה ל-FAILED רק בגלל
        # שקוד היציאה של תהליך שקיבל SIGTERM לרוב לא 0.
        if _get_status(app_id) == AppStatus.STOPPED:
            _emit(app_id, loop, "[serves] process stopped")
            return
        if code == 0:
            _set_status(app_id, AppStatus.STOPPED)
            _emit(app_id, loop, "[serves] process exited (exit code 0)")
        else:
            _set_status(app_id, AppStatus.FAILED, error=f"process exited with code {code}")
            _emit(app_id, loop, f"[serves] process failed (exit code {code})")

    runtime.stream_logs(handle, on_line, on_exit)


def _launch(
    app_id: int,
    loop: asyncio.AbstractEventLoop,
    code_dir: Path,
    requirements_file: str,
    run_command: str,
    env_vars: dict,
    resources: dict,
    emit,
    use_dockerfile: bool = False,
) -> None:
    """מריץ קוד שכבר קיים בדיסק (משותף בין deploy() ל-start_app())."""
    _fix_ownership(code_dir)

    runtime.ensure_ready()
    emit("[serves] building Dockerfile ..." if use_dockerfile else "[serves] installing dependencies and starting ...")
    handle = runtime.start(
        app_id, code_dir, requirements_file, run_command, env_vars,
        memory_mb=resources["memory_mb"], cpu_cores=resources["cpu_cores"],
        bandwidth_mbps=resources["bandwidth_mbps"], use_dockerfile=use_dockerfile,
    )

    _set_status(app_id, AppStatus.RUNNING, container_id=handle)
    emit("[serves] application is running in the background")

    try:
        username = telegram_service.find_bot_username(env_vars)
    except Exception:
        username = None
    if username:
        _set_telegram_username(app_id, username)
        emit(f"[serves] detected Telegram bot: @{username}")

    threading.Thread(target=_watch, args=(app_id, handle, loop), daemon=True).start()


def deploy(app_id: int, loop: asyncio.AbstractEventLoop) -> None:
    """פונקציה חוסמת - להריץ כ-background task, לא בתוך ה-event loop הראשי.
    משכפלת את הריפו מחדש מאפס ואז מריצה. לשחזור הרצה בלי לשכפל מחדש -
    ראו start_app()."""
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id)
        if not app:
            return
        app.status = AppStatus.BUILDING
        app.last_error = None
        db.commit()

        source_type = app.source_type or "git"
        repo_url = app.repo_url
        branch = (app.branch or "").strip()
        use_dockerfile = app.use_dockerfile
        requirements_file = (app.requirements_file or "requirements.txt").strip()
        run_command = app.run_command
        env_vars = dict(app.env_vars or {})
        resources = _plan_resources(app.owner.plan)
    finally:
        db.close()

    log_broadcaster.clear_log(app_id)

    def emit(line: str) -> None:
        _emit(app_id, loop, line)

    code_dir = app_code_dir(app_id)
    try:
        _ensure_app_volume(app_id, resources["disk_mb"])
        _clear_dir_contents(code_dir)

        if source_type == "zip":
            zip_path = source_zip_path(app_id)
            emit("[serves] extracting uploaded zip file ...")
            if not zip_path.exists():
                raise RuntimeError("no uploaded zip file found for this application")
            _extract_zip_safely(zip_path, code_dir)
        else:
            clone_cmd = ["git", "clone", "--depth", "1"]
            if branch:
                clone_cmd += ["--branch", branch]
            clone_cmd += [repo_url, str(code_dir)]
            emit(f"[serves] cloning {repo_url} (branch: {branch or 'default'}) ...")
            result = subprocess.run(
                clone_cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            for out_line in (result.stdout + result.stderr).splitlines():
                emit(out_line)
            if result.returncode != 0:
                raise RuntimeError("git clone failed - check that the repo link is public and correct")

        if use_dockerfile:
            # ב-Dockerfile מותאם-אישית אין requirements.txt/run_command לבדוק -
            # ה-Dockerfile עצמו קובע איך בונים ומריצים (ראו הרחבה בתיעוד
            # ב-DockerRuntime.start).
            if not (code_dir / "Dockerfile").exists():
                raise RuntimeError("No Dockerfile found at the root of the uploaded/cloned source")
        else:
            req_path = code_dir / requirements_file
            if req_path.exists():
                check_requirements(req_path.read_text(encoding="utf-8", errors="replace"))
            check_run_command(run_command)

        _launch(app_id, loop, code_dir, requirements_file, run_command, env_vars, resources, emit, use_dockerfile=use_dockerfile)

    except PolicyViolation as exc:
        _set_status(app_id, AppStatus.FAILED, error=exc.message)
        emit(f"[serves] ERROR: {exc.message}")
    except Exception as exc:  # noqa: BLE001 - כל כשל בפריסה מדווח למשתמש בלוג
        _set_status(app_id, AppStatus.FAILED, error=str(exc))
        emit(f"[serves] ERROR: {exc}")


def start_app(app_id: int, loop: asyncio.AbstractEventLoop) -> None:
    """מפעילה מחדש אפליקציה שכבר נפרסה (למשל אחרי עצירה), בלי לשכפל את
    הריפו מחדש - מהירה יותר מ-deploy(). אם אין קוד על הדיסק בכלל
    (מעולם לא נפרס בהצלחה), נופלת חזרה ל-deploy() מלא."""
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id)
        if not app:
            return
        use_dockerfile = app.use_dockerfile
        requirements_file = (app.requirements_file or "requirements.txt").strip()
        run_command = app.run_command
        env_vars = dict(app.env_vars or {})
        resources = _plan_resources(app.owner.plan)
    finally:
        db.close()

    code_dir = app_code_dir(app_id)
    _ensure_app_volume(app_id, resources["disk_mb"])
    if not any(code_dir.iterdir()):
        # אין קוד בכלל (מעולם לא נפרס, או שה-volume אבד/התאפס) - fallback ל-deploy מלא
        deploy(app_id, loop)
        return

    def emit(line: str) -> None:
        _emit(app_id, loop, line)

    try:
        if use_dockerfile:
            if not (code_dir / "Dockerfile").exists():
                raise RuntimeError("No Dockerfile found at the root of the uploaded/cloned source")
        else:
            req_path = code_dir / requirements_file
            if req_path.exists():
                check_requirements(req_path.read_text(encoding="utf-8", errors="replace"))
            check_run_command(run_command)
        _launch(app_id, loop, code_dir, requirements_file, run_command, env_vars, resources, emit, use_dockerfile=use_dockerfile)
    except PolicyViolation as exc:
        _set_status(app_id, AppStatus.FAILED, error=exc.message)
        emit(f"[serves] ERROR: {exc.message}")
    except Exception as exc:  # noqa: BLE001
        _set_status(app_id, AppStatus.FAILED, error=str(exc))
        emit(f"[serves] ERROR: {exc}")


def stop_app(app: BotApp) -> None:
    if app.container_id:
        runtime.stop(app.container_id)
    _set_status(app.id, AppStatus.STOPPED)


def teardown_app(app: BotApp) -> None:
    if app.container_id:
        runtime.remove(app.container_id)
    log_broadcaster.clear_log(app.id)
    _release_app_volume(app.id)
    root_dir = app_root_dir(app.id)
    if root_dir.exists():
        shutil.rmtree(root_dir, ignore_errors=True)
