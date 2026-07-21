"""
מעביר תעבורת HTTP מהאינטרנט אל שרת האינטרנט של האפליקציה עצמה (אם יש כזה
- ראו PORT ב-docker_manager.py), כדי שאפליקציות שהן לא רק בוט טלגרם אלא
גם מפעילות אתר יהיו נגישות תחת /open/<slug>. הנתיב הזה ציבורי במתכוון
(כמו כל אתר - אין התחברות נדרשת לצפייה בו). אם האפליקציה לא רצה, או
שאין לה שרת אינטרנט שמאזין על הפורט השמור, מוצג דף מיתוג במקום שגיאה.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppStatus, BotApp
from app.services.docker_manager import runtime
from app.web_utils import render

router = APIRouter()

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
    "host",
}


async def _proxy(request: Request, slug: str, path: str, db: Session):
    app = db.query(BotApp).filter(BotApp.slug == slug).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    # קישור ציבורי לאתר האפליקציה זמין רק לתוכניות בתשלום (Pro/Plus) -
    # נאכף כאן ולא רק ב-UI, כדי שלא יהיה משנה איך מגיעים לכתובת (path
    # תחת הדומיין הראשי, או סאב-דומיין אם מופעל).
    if app.owner.plan == "free":
        return render(request, "app_placeholder.html", app=app, reason="plan_required", status_code=200)

    address = None
    if app.status == AppStatus.RUNNING and app.container_id:
        address = runtime.get_internal_address(app.container_id)

    if not address:
        reason = "not_running" if app.status != AppStatus.RUNNING else "no_server"
        return render(request, "app_placeholder.html", app=app, reason=reason, status_code=200)

    ip, port = address
    target_url = f"http://{ip}:{port}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target_url,
                params=request.url.query or None,
                headers=headers,
                content=body,
            )
    except httpx.RequestError:
        return render(request, "app_placeholder.html", app=app, reason="no_server", status_code=200)

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


@router.api_route("/open/{slug}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def open_app_root(request: Request, slug: str, db: Session = Depends(get_db)):
    return await _proxy(request, slug, "", db)


@router.api_route("/open/{slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def open_app_path(request: Request, slug: str, path: str, db: Session = Depends(get_db)):
    return await _proxy(request, slug, path, db)
