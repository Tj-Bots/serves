import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import AuthRedirect
from app.config import settings
from app.database import init_db
from app.i18n import SUPPORTED_LANGS
from app.routers import apps, auth, logs_ws
from app.services.docker_manager import runtime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("serves")

app = FastAPI(title="Serves - Telegram Bot Hosting")

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, same_site="lax")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(apps.router)
app.include_router(logs_ws.router)


@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect):
    return RedirectResponse(exc.to, status_code=303)


@app.get("/lang/{lang_code}")
def set_language(lang_code: str, request: Request, next: str = "/"):
    if lang_code in SUPPORTED_LANGS:
        request.session["lang"] = lang_code
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(safe_next, status_code=303)


@app.on_event("startup")
def on_startup():
    init_db()
    try:
        runtime.ensure_ready()
    except Exception as exc:  # noqa: BLE001 - לא מפילים את השרת אם Docker עדיין לא מוכן
        logger.warning("runtime.ensure_ready() failed: %s", exc)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=False)
