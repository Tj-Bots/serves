import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import AuthRedirect
from app.config import settings
from app.database import SessionLocal, init_db
from app.i18n import SUPPORTED_LANGS
from app.routers import account, apps, auth, billing, logs_ws, proxy
from app.routers.proxy import _proxy as proxy_request
from app.services import payment_bot
from app.services.docker_manager import runtime
from app.slugs import RESERVED_SLUGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("serves")

app = FastAPI(title="Serves - Telegram Bot Hosting")


@app.middleware("http")
async def app_subdomain_proxy(request: Request, call_next):
    """אם ל-PUBLIC_BASE_URL יש דומיין (למשל teleboss.online) ובקשה מגיעה
    עם Host שהוא סאב-דומיין שלו (למשל my-bot.teleboss.online), מזרימים
    אותה ישירות לקונטיינר של האפליקציה המתאימה - בלי לעבור בכלל דרך
    שאר הראוטים של האתר (login/dashboard וכו', שרלוונטיים רק לדומיין
    הראשי). אם אין רשומת DNS wildcard מוגדרת בפועל, בקשות כאלה פשוט
    לא מגיעות לשרת מלכתחילה - כך שאין בעיה שזה תמיד "דלוק"."""
    base_domain = settings.APPS_BASE_DOMAIN
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if base_domain and host != base_domain and host.endswith("." + base_domain):
        slug = host[: -(len(base_domain) + 1)]
        if slug and "." not in slug and slug not in RESERVED_SLUGS:
            db = SessionLocal()
            try:
                return await proxy_request(request, slug, request.url.path.lstrip("/"), db)
            except HTTPException as exc:
                return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
            finally:
                db.close()
    return await call_next(request)


# נרשם אחרי app_subdomain_proxy בכוונה: ב-Starlette המידלוור שנרשם אחרון
# הוא זה שעוטף הכי מבחוץ ורץ ראשון - כדי ש-request.session יהיה זמין
# כבר בתוך app_subdomain_proxy (render() של app_placeholder.html משתמש
# בזה כדי לזהות שפה), SessionMiddleware חייב לרוץ *לפניו*.
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, same_site="lax")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(apps.router)
app.include_router(account.router)
app.include_router(billing.router)
app.include_router(logs_ws.router)
app.include_router(proxy.router)


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
async def on_startup():
    init_db()
    try:
        runtime.ensure_ready()
    except Exception as exc:  # noqa: BLE001 - לא מפילים את השרת אם Docker עדיין לא מוכן
        logger.warning("runtime.ensure_ready() failed: %s", exc)

    app.state.payment_bot_task = asyncio.create_task(payment_bot.run_polling())


@app.on_event("shutdown")
async def on_shutdown():
    task = getattr(app.state, "payment_bot_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=False)
