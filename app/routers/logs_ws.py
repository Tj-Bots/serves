from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import SESSION_KEY
from app.database import SessionLocal
from app.models import BotApp
from app.services import log_broadcaster

router = APIRouter()


@router.websocket("/ws/logs/{app_id}")
async def logs_ws(websocket: WebSocket, app_id: int):
    user_id = websocket.session.get(SESSION_KEY)
    db = SessionLocal()
    try:
        app = db.get(BotApp, app_id) if user_id else None
        authorized = bool(user_id and app and app.user_id == user_id)
    finally:
        db.close()

    if not authorized:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    for line in log_broadcaster.read_tail(app_id):
        await websocket.send_text(line)

    queue = log_broadcaster.subscribe(app_id)
    try:
        while True:
            line = await queue.get()
            await websocket.send_text(line)
    except WebSocketDisconnect:
        pass
    finally:
        log_broadcaster.unsubscribe(app_id, queue)
