"""
שירות הפריסה: משכפל את הריפו, בודק מדיניות אבטחה, ומריץ את הבוט דרך
שכבת ה-runtime (Docker בפרודקשן). רץ כ-background task אחרי יצירת/עדכון
אפליקציה, כדי שהבקשה ה-HTTP תחזור מיד והלוגים יופיעו בזמן אמת ב-UI.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models import AppStatus, BotApp
from app.security_policy import PolicyViolation, check_requirements, check_run_command
from app.services import log_broadcaster
from app.services.docker_manager import runtime


def app_root_dir(app_id: int) -> Path:
    return settings.APPS_DIR / str(app_id)


def app_code_dir(app_id: int) -> Path:
    return app_root_dir(app_id) / "code"


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


def _watch(app_id: int, handle: str, loop: asyncio.AbstractEventLoop) -> None:
    def on_line(line: str) -> None:
        _emit(app_id, loop, line)

    def on_exit(code: int) -> None:
        if code == 0:
            _set_status(app_id, AppStatus.STOPPED)
            _emit(app_id, loop, "[serves] התהליך הסתיים (קוד יציאה 0)")
        else:
            _set_status(app_id, AppStatus.FAILED, error=f"process exited with code {code}")
            _emit(app_id, loop, f"[serves] התהליך נכשל (קוד יציאה {code})")

    runtime.stream_logs(handle, on_line, on_exit)


def deploy(app_id: int, loop: asyncio.AbstractEventLoop) -> None:
    """פונקציה חוסמת - להריץ כ-background task, לא בתוך ה-event loop הראשי."""
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id)
        if not app:
            return
        app.status = AppStatus.BUILDING
        app.last_error = None
        db.commit()

        repo_url = app.repo_url
        requirements_file = (app.requirements_file or "requirements.txt").strip()
        run_command = app.run_command
        env_vars = dict(app.env_vars or {})
    finally:
        db.close()

    log_broadcaster.clear_log(app_id)

    def emit(line: str) -> None:
        _emit(app_id, loop, line)

    code_dir = app_code_dir(app_id)
    try:
        emit(f"[serves] משכפל {repo_url} ...")
        if code_dir.exists():
            shutil.rmtree(code_dir)
        code_dir.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(code_dir)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        for out_line in (result.stdout + result.stderr).splitlines():
            emit(out_line)
        if result.returncode != 0:
            raise RuntimeError("git clone נכשל - בדוק שהקישור לריפו ציבורי ותקין")

        req_path = code_dir / requirements_file
        if req_path.exists():
            check_requirements(req_path.read_text(encoding="utf-8", errors="replace"))
        check_run_command(run_command)

        runtime.ensure_ready()
        emit("[serves] מתקין תלויות ומריץ ...")
        handle = runtime.start(app_id, code_dir, requirements_file, run_command, env_vars)

        _set_status(app_id, AppStatus.RUNNING, container_id=handle)
        emit("[serves] האפליקציה רצה ברקע")

        threading.Thread(target=_watch, args=(app_id, handle, loop), daemon=True).start()

    except PolicyViolation as exc:
        _set_status(app_id, AppStatus.FAILED, error=exc.message)
        emit(f"[serves] שגיאה: {exc.message}")
    except Exception as exc:  # noqa: BLE001 - כל כשל בפריסה מדווח למשתמש בלוג
        _set_status(app_id, AppStatus.FAILED, error=str(exc))
        emit(f"[serves] שגיאה: {exc}")


def stop_app(app: BotApp) -> None:
    if app.container_id:
        runtime.stop(app.container_id)
    _set_status(app.id, AppStatus.STOPPED)


def teardown_app(app: BotApp) -> None:
    if app.container_id:
        runtime.remove(app.container_id)
    log_broadcaster.clear_log(app.id)
    root_dir = app_root_dir(app.id)
    if root_dir.exists():
        shutil.rmtree(root_dir, ignore_errors=True)
