"""
בוט תשלומים נפרד (Telegram Stars) - רץ כ-asyncio background task בתוך אותו
תהליך של הפלטפורמה (long polling מול Telegram, לא webhook - אין צורך
בשירות/דומיין נפרד). זרימת התשלום:

1. באתר: /billing/upgrade/<plan> יוצר PlanPurchase עם pay_code אקראי
   ובלתי-ניתן-לניחוש, ומפנה את הדפדפן לעמוד עם קישור עומק לבוט הזה:
   https://t.me/<username>?start=pay_<code>
2. הבוט מקבל /start pay_<code>, מוצא את ה-PlanPurchase המתאים (אם לא
   נמצא/פג תוקף - מסרב), ושולח sendInvoice עם currency=XTR (כוכבי טלגרם).
3. Telegram שולח pre_checkout_query - הבוט מאשר רק אם הרכישה עדיין
   pending ולא פגה.
4. אחרי תשלום אמיתי, Telegram שולח successful_payment - רק אז (לא לפני!)
   מסמנים PAID ומעדכנים את plan המשתמש. האתר לא סומך על שום דבר שהגיע
   מהדפדפן - רק על האירוע הזה מ-Telegram.
"""

import asyncio
import datetime
import logging

import httpx
from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import AppStatus, BotApp, PlanPurchase, PurchaseStatus, User
from app.services import deploy as deploy_service

logger = logging.getLogger("serves.payment_bot")


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.PAYMENT_BOT_TOKEN}/{method}"


async def _call(client: httpx.AsyncClient, method: str, **params):
    resp = await client.post(_api_url(method), json=params, timeout=35)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        logger.warning("telegram API %s returned not-ok: %s", method, data)
    return data


def _is_expired(purchase: PlanPurchase) -> bool:
    created = purchase.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
    return age > settings.PAYMENT_LINK_TTL_MINUTES * 60


async def _handle_start(client: httpx.AsyncClient, message: dict) -> None:
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if not payload.startswith("pay_"):
        reply_markup = None
        if settings.PUBLIC_BASE_URL:
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "Open Serves", "url": settings.PUBLIC_BASE_URL.rstrip("/") + "/billing"}
                ]]
            }
        await _call(
            client, "sendMessage", chat_id=chat_id,
            text=(
                "👋 This is the official payment bot for Serves (teleboss.online).\n\n"
                "It only handles plan upgrades paid with Telegram Stars - it doesn't "
                "host or run anything itself. To upgrade, open the Billing page on "
                "the website and tap Upgrade - you'll be sent back here automatically "
                "with a payment link."
            ),
            reply_markup=reply_markup,
        )
        return

    pay_code = payload[len("pay_"):]
    db = SessionLocal()
    try:
        purchase = db.query(PlanPurchase).filter(PlanPurchase.pay_code == pay_code).first()
        if not purchase or purchase.status != PurchaseStatus.PENDING:
            await _call(
                client, "sendMessage", chat_id=chat_id,
                text="This payment link is invalid or was already used. Start a new upgrade from the Serves dashboard.",
            )
            return
        if _is_expired(purchase):
            purchase.status = PurchaseStatus.EXPIRED
            db.commit()
            await _call(
                client, "sendMessage", chat_id=chat_id,
                text="This payment link has expired. Start a new upgrade from the Serves dashboard.",
            )
            return

        purchase.telegram_chat_id = str(chat_id)
        db.commit()

        plan_label = purchase.plan_name.capitalize()
        await _call(
            client, "sendInvoice",
            chat_id=chat_id,
            title=f"Serves {plan_label} plan",
            description=f"Upgrade your Serves account to the {plan_label} plan.",
            payload=pay_code,
            provider_token="",  # ריק זה תקין ל-Telegram Stars (XTR)
            currency="XTR",
            prices=[{"label": f"{plan_label} plan", "amount": purchase.stars_amount}],
        )
    finally:
        db.close()


async def _handle_pre_checkout(client: httpx.AsyncClient, query: dict) -> None:
    pay_code = query.get("invoice_payload", "")
    db = SessionLocal()
    try:
        purchase = db.query(PlanPurchase).filter(PlanPurchase.pay_code == pay_code).first()
        ok = bool(purchase) and purchase.status == PurchaseStatus.PENDING and not _is_expired(purchase)
        kwargs = {"pre_checkout_query_id": query["id"], "ok": ok}
        if not ok:
            kwargs["error_message"] = "This purchase is no longer valid. Start a new upgrade from the dashboard."
        await _call(client, "answerPreCheckoutQuery", **kwargs)
    finally:
        db.close()


async def _handle_successful_payment(client: httpx.AsyncClient, message: dict) -> None:
    payment = message["successful_payment"]
    pay_code = payment.get("invoice_payload", "")
    chat_id = message["chat"]["id"]

    db = SessionLocal()
    try:
        purchase = db.query(PlanPurchase).filter(PlanPurchase.pay_code == pay_code).first()
        if not purchase:
            logger.error("successful_payment for unknown pay_code=%s", pay_code)
            return
        purchase.status = PurchaseStatus.PAID
        purchase.paid_at = datetime.datetime.now(datetime.timezone.utc)
        user = db.get(User, purchase.user_id)
        if user:
            user.plan = purchase.plan_name
        db.commit()
    finally:
        db.close()

    reply_markup = None
    if settings.PUBLIC_BASE_URL:
        reply_markup = {
            "inline_keyboard": [[{"text": "Open dashboard", "url": settings.PUBLIC_BASE_URL.rstrip("/") + "/dashboard"}]]
        }
    await _call(
        client, "sendMessage", chat_id=chat_id,
        text="✅ Payment received! Your plan has been upgraded.",
        reply_markup=reply_markup,
    )


"""
--- פאנל ניהול דרך הבוט (/admin) ---

מוגבל למזהי טלגרם ב-ADMIN_TELEGRAM_IDS בלבד (לא username - המספר
המספרי, ניתן לקבל מ-@userinfobot). כל התפריטים נבנים עם inline keyboard
(callback_query), לא קלט טקסט חופשי, כדי למנוע צורך במעקב אחרי מצב
שיחה. פעולות הרסניות (מחיקת חשבון/אפליקציה) דורשות אישור בלחיצה נוספת.
"""

ADMIN_MAIN_MENU = {
    "inline_keyboard": [
        [{"text": "📊 Stats", "callback_data": "adm:stats"}],
        [{"text": "👥 Users", "callback_data": "adm:users"}],
        [{"text": "📦 Apps", "callback_data": "adm:apps"}],
    ]
}


def _is_admin(chat_id) -> bool:
    return chat_id in settings.ADMIN_TELEGRAM_IDS


def _back_menu(text: str = "« Back", data: str = "adm:menu") -> dict:
    return {"inline_keyboard": [[{"text": text, "callback_data": data}]]}


async def _handle_admin_command(client: httpx.AsyncClient, message: dict) -> None:
    chat_id = message["chat"]["id"]
    if not _is_admin(chat_id):
        return
    await _call(client, "sendMessage", chat_id=chat_id, text="🛠 Serves Admin", reply_markup=ADMIN_MAIN_MENU)


async def _send_stats(client: httpx.AsyncClient, chat_id) -> None:
    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        blocked = db.query(User).filter(User.is_blocked.is_(True)).count()
        total_apps = db.query(BotApp).count()
        running = db.query(BotApp).filter(BotApp.status == AppStatus.RUNNING).count()
        plan_counts = dict(db.query(User.plan, func.count(User.id)).group_by(User.plan).all())
    finally:
        db.close()
    lines = [
        "📊 Stats",
        f"Accounts: {total_users} (blocked: {blocked})",
        f"Apps: {total_apps} (running: {running})",
        "Plans: " + ", ".join(f"{k}={v}" for k, v in plan_counts.items()),
    ]
    await _call(client, "sendMessage", chat_id=chat_id, text="\n".join(lines), reply_markup=_back_menu())


async def _send_users(client: httpx.AsyncClient, chat_id) -> None:
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.created_at.desc()).limit(10).all()
        rows = [
            [{"text": f"{'🚫' if u.is_blocked else '✅'} {u.email} ({u.plan})", "callback_data": f"adm:u:{u.id}"}]
            for u in users
        ]
    finally:
        db.close()
    rows.append([{"text": "« Back", "callback_data": "adm:menu"}])
    await _call(
        client, "sendMessage", chat_id=chat_id,
        text="👥 Latest 10 accounts (tap to manage - use the web admin panel to search):",
        reply_markup={"inline_keyboard": rows},
    )


async def _send_user_detail(client: httpx.AsyncClient, chat_id, user_id: int) -> None:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if not u:
            await _call(client, "sendMessage", chat_id=chat_id, text="User not found.")
            return
        text = (
            f"👤 {u.first_name} {u.last_name}\n{u.email}\n"
            f"Plan: {u.plan} · Apps: {len(u.apps)}\n"
            f"Blocked: {'yes' if u.is_blocked else 'no'} · Verified: {'yes' if u.is_verified else 'no'}"
        )
        block_btn = (
            {"text": "✅ Unblock", "callback_data": f"adm:uunblk:{u.id}"}
            if u.is_blocked
            else {"text": "🚫 Block", "callback_data": f"adm:ublk:{u.id}"}
        )
    finally:
        db.close()
    rows = [
        [block_btn],
        [{"text": "🗑 Delete account", "callback_data": f"adm:udelc:{user_id}"}],
        [{"text": "« Back to users", "callback_data": "adm:users"}],
    ]
    await _call(client, "sendMessage", chat_id=chat_id, text=text, reply_markup={"inline_keyboard": rows})


async def _set_user_blocked(client: httpx.AsyncClient, chat_id, user_id: int, blocked: bool) -> None:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if u:
            u.is_blocked = blocked
            db.commit()
    finally:
        db.close()
    await _send_user_detail(client, chat_id, user_id)


async def _delete_user(client: httpx.AsyncClient, chat_id, user_id: int) -> None:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if u:
            for a in list(u.apps):
                deploy_service.teardown_app(a)
            db.delete(u)
            db.commit()
    finally:
        db.close()
    await _call(client, "sendMessage", chat_id=chat_id, text="🗑 Account deleted.", reply_markup=_back_menu("« Users", "adm:users"))


async def _send_apps(client: httpx.AsyncClient, chat_id) -> None:
    db = SessionLocal()
    try:
        apps = db.query(BotApp).order_by(BotApp.created_at.desc()).limit(10).all()
        rows = [
            [{"text": f"{'⏸' if a.admin_suspended else '▶' if a.status == AppStatus.RUNNING else '⏹'} {a.name}", "callback_data": f"adm:a:{a.id}"}]
            for a in apps
        ]
    finally:
        db.close()
    rows.append([{"text": "« Back", "callback_data": "adm:menu"}])
    await _call(
        client, "sendMessage", chat_id=chat_id,
        text="📦 Latest 10 apps (tap to manage - use the web admin panel to search):",
        reply_markup={"inline_keyboard": rows},
    )


async def _send_app_detail(client: httpx.AsyncClient, chat_id, app_id: int) -> None:
    db = SessionLocal()
    try:
        a = db.get(BotApp, app_id)
        if not a:
            await _call(client, "sendMessage", chat_id=chat_id, text="App not found.")
            return
        text = (
            f"📦 {a.name}\nOwner: {a.owner.email} ({a.owner.plan})\nStatus: {a.status.value}\n"
            + (f"Suspended: {a.admin_suspend_reason or 'yes'}\n" if a.admin_suspended else "")
            + (f"Bot: @{a.telegram_username}" if a.telegram_username else "No Telegram bot detected")
        )
        suspend_btn = (
            {"text": "▶️ Unsuspend", "callback_data": f"adm:aunsusp:{a.id}"}
            if a.admin_suspended
            else {"text": "⏸ Suspend", "callback_data": f"adm:asusp:{a.id}"}
        )
    finally:
        db.close()
    rows = [
        [suspend_btn],
        [{"text": "🗑 Delete app", "callback_data": f"adm:adelc:{app_id}"}],
        [{"text": "« Back to apps", "callback_data": "adm:apps"}],
    ]
    await _call(client, "sendMessage", chat_id=chat_id, text=text, reply_markup={"inline_keyboard": rows})


async def _set_app_suspended(client: httpx.AsyncClient, chat_id, app_id: int, suspended: bool) -> None:
    db = SessionLocal()
    try:
        a = db.get(BotApp, app_id)
        if a:
            if suspended:
                deploy_service.stop_app(a)
                a.admin_suspend_reason = "Suspended via Telegram admin"
            else:
                a.admin_suspend_reason = None
            a.admin_suspended = suspended
            db.commit()
    finally:
        db.close()
    await _send_app_detail(client, chat_id, app_id)


async def _delete_app(client: httpx.AsyncClient, chat_id, app_id: int) -> None:
    db = SessionLocal()
    try:
        a = db.get(BotApp, app_id)
        if a:
            deploy_service.teardown_app(a)
            db.delete(a)
            db.commit()
    finally:
        db.close()
    await _call(client, "sendMessage", chat_id=chat_id, text="🗑 App deleted.", reply_markup=_back_menu("« Apps", "adm:apps"))


async def _handle_admin_callback(client: httpx.AsyncClient, query: dict) -> None:
    chat_id = query["message"]["chat"]["id"]
    data = query.get("data", "")
    await _call(client, "answerCallbackQuery", callback_query_id=query["id"])
    if not _is_admin(chat_id):
        return

    if data == "adm:menu":
        await _call(client, "sendMessage", chat_id=chat_id, text="🛠 Serves Admin", reply_markup=ADMIN_MAIN_MENU)
    elif data == "adm:stats":
        await _send_stats(client, chat_id)
    elif data == "adm:users":
        await _send_users(client, chat_id)
    elif data == "adm:apps":
        await _send_apps(client, chat_id)
    elif data.startswith("adm:u:"):
        await _send_user_detail(client, chat_id, int(data.split(":")[2]))
    elif data.startswith("adm:ublk:"):
        await _set_user_blocked(client, chat_id, int(data.split(":")[2]), True)
    elif data.startswith("adm:uunblk:"):
        await _set_user_blocked(client, chat_id, int(data.split(":")[2]), False)
    elif data.startswith("adm:udelc:"):
        uid = int(data.split(":")[2])
        rows = [[
            {"text": "⚠️ Yes, delete", "callback_data": f"adm:udel:{uid}"},
            {"text": "Cancel", "callback_data": f"adm:u:{uid}"},
        ]]
        await _call(
            client, "sendMessage", chat_id=chat_id,
            text="Delete this account and ALL its apps? This cannot be undone.",
            reply_markup={"inline_keyboard": rows},
        )
    elif data.startswith("adm:udel:"):
        await _delete_user(client, chat_id, int(data.split(":")[2]))
    elif data.startswith("adm:a:"):
        await _send_app_detail(client, chat_id, int(data.split(":")[2]))
    elif data.startswith("adm:asusp:"):
        await _set_app_suspended(client, chat_id, int(data.split(":")[2]), True)
    elif data.startswith("adm:aunsusp:"):
        await _set_app_suspended(client, chat_id, int(data.split(":")[2]), False)
    elif data.startswith("adm:adelc:"):
        aid = int(data.split(":")[2])
        rows = [[
            {"text": "⚠️ Yes, delete", "callback_data": f"adm:adel:{aid}"},
            {"text": "Cancel", "callback_data": f"adm:a:{aid}"},
        ]]
        await _call(
            client, "sendMessage", chat_id=chat_id,
            text="Delete this app permanently?", reply_markup={"inline_keyboard": rows},
        )
    elif data.startswith("adm:adel:"):
        await _delete_app(client, chat_id, int(data.split(":")[2]))


async def _dispatch(client: httpx.AsyncClient, update: dict) -> None:
    message = update.get("message")
    if message and "successful_payment" in message:
        await _handle_successful_payment(client, message)
    elif message and message.get("text", "").startswith("/admin"):
        await _handle_admin_command(client, message)
    elif message and message.get("text", "").startswith("/start"):
        await _handle_start(client, message)
    elif "pre_checkout_query" in update:
        await _handle_pre_checkout(client, update["pre_checkout_query"])
    elif "callback_query" in update:
        await _handle_admin_callback(client, update["callback_query"])


async def run_polling() -> None:
    if not settings.PAYMENT_BOT_TOKEN:
        logger.info("PAYMENT_BOT_TOKEN not configured - payment bot disabled")
        return

    logger.info("payment bot starting (long polling)")
    offset = 0
    async with httpx.AsyncClient() as client:
        while True:
            try:
                data = await _call(client, "getUpdates", offset=offset, timeout=25)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("payment bot getUpdates failed, retrying in 5s")
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    await _dispatch(client, update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("payment bot: error handling update %s", update.get("update_id"))
