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
import html
import logging

import httpx
from sqlalchemy import func

from app.config import PLANS, settings
from app.database import SessionLocal
from app.models import AppStatus, BotApp, PlanPurchase, PurchaseStatus, User
from app.promo import create_promo_code
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
                    {"text": "Open TeleBoss", "url": settings.PUBLIC_BASE_URL.rstrip("/") + "/billing"}
                ]]
            }
        await _call(
            client, "sendMessage", chat_id=chat_id,
            text=(
                "👋 This is the official payment bot for TeleBoss (teleboss.online).\n\n"
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
                text="This payment link is invalid or was already used. Start a new upgrade from the TeleBoss dashboard.",
            )
            return
        if _is_expired(purchase):
            purchase.status = PurchaseStatus.EXPIRED
            db.commit()
            await _call(
                client, "sendMessage", chat_id=chat_id,
                text="This payment link has expired. Start a new upgrade from the TeleBoss dashboard.",
            )
            return

        purchase.telegram_chat_id = str(chat_id)
        db.commit()

        plan_label = purchase.plan_name.capitalize()
        await _call(
            client, "sendInvoice",
            chat_id=chat_id,
            title=f"TeleBoss {plan_label} plan",
            description=f"Upgrade your TeleBoss account to the {plan_label} plan.",
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
לחיצה על כפתור עורכת את ההודעה הקיימת במקום (editMessageText) - לא
שולחת הודעה חדשה בכל פעם - עם עיצוב HTML (מודגש/נטוי/קוד).
"""

PAGE_SIZE = 10

ADMIN_MAIN_MENU = {
    "inline_keyboard": [
        [{"text": "📊 Stats", "callback_data": "adm:stats"}],
        [{"text": "👥 Users", "callback_data": "adm:users:0"}],
        [{"text": "📦 Apps", "callback_data": "adm:apps:0"}],
        [{"text": "🎟 Generate code", "callback_data": "adm:codes"}],
    ]
}


def _is_admin(chat_id) -> bool:
    return chat_id in settings.ADMIN_TELEGRAM_IDS


def _esc(text) -> str:
    return html.escape(str(text)) if text is not None else ""


def _back_row(text: str = "« Back", data: str = "adm:menu") -> list[dict]:
    return [{"text": text, "callback_data": data}]


def _page_nav_row(prefix: str, page: int, has_next: bool) -> list[dict]:
    """שורת ניווט: בעמוד הראשון רק "Next", בעמודים באמצע גם וגם, בעמוד
    האחרון רק "Prev" - כי כל צד מוצג רק אם יש לאן לזוז אליו. הבוט כולו
    באנגלית בכוונה (ראו docstring למעלה)."""
    row = []
    if page > 0:
        row.append({"text": "◀ Prev", "callback_data": f"{prefix}:{page - 1}"})
    if has_next:
        row.append({"text": "Next ▶", "callback_data": f"{prefix}:{page + 1}"})
    return row


async def _reply(client: httpx.AsyncClient, chat_id, message_id, text: str, reply_markup: dict | None = None) -> None:
    """עורך את ההודעה הקיימת (אם באנו מלחיצה על כפתור) או שולח הודעה
    חדשה (אם זו הפעלה ראשונה של /admin, שאין לה הודעה קודמת לערוך)."""
    if message_id is not None:
        await _call(
            client, "editMessageText", chat_id=chat_id, message_id=message_id,
            text=text, reply_markup=reply_markup, parse_mode="HTML",
        )
    else:
        await _call(client, "sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")


async def _handle_admin_command(client: httpx.AsyncClient, message: dict) -> None:
    chat_id = message["chat"]["id"]
    if not _is_admin(chat_id):
        return
    await _reply(client, chat_id, None, "<b>🛠 TeleBoss Admin</b>", ADMIN_MAIN_MENU)


async def _send_stats(client: httpx.AsyncClient, chat_id, message_id) -> None:
    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        blocked = db.query(User).filter(User.is_blocked.is_(True)).count()
        total_apps = db.query(BotApp).count()
        running = db.query(BotApp).filter(BotApp.status == AppStatus.RUNNING).count()
        plan_counts = dict(db.query(User.plan, func.count(User.id)).group_by(User.plan).all())
    finally:
        db.close()
    plans_line = ", ".join(f"<b>{_esc(k)}</b>: {v}" for k, v in plan_counts.items())
    text = (
        "<b>📊 Stats</b>\n\n"
        f"<b>Accounts:</b> {total_users} <i>(blocked: {blocked})</i>\n"
        f"<b>Apps:</b> {total_apps} <i>(running: {running})</i>\n"
        f"<b>Plans:</b> {plans_line}"
    )
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": [_back_row()]})


async def _send_code_menu(client: httpx.AsyncClient, chat_id, message_id) -> None:
    rows = [
        [{"text": f"🎟 Generate {name.capitalize()} code", "callback_data": f"adm:gencode:{name}"}]
        for name, plan in PLANS.items()
        if plan["stars"] > 0
    ]
    rows.append(_back_row())
    text = "<b>🎟 Redeem codes</b>\n\nGenerate a single-use code (expires in 30 days) that grants a paid plan for free:"
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": rows})


async def _generate_code(client: httpx.AsyncClient, chat_id, message_id, plan_name: str) -> None:
    plan = PLANS.get(plan_name)
    if not plan or plan["stars"] <= 0:
        await _reply(client, chat_id, message_id, "Invalid plan.", {"inline_keyboard": [_back_row("« Codes", "adm:codes")]})
        return
    db = SessionLocal()
    try:
        promo = create_promo_code(db, plan_name, chat_id)
        code_str = promo.code
    finally:
        db.close()
    text = (
        f"<b>🎟 New {_esc(plan_name.capitalize())} code</b>\n\n"
        f"<code>{_esc(code_str)}</code>\n\n"
        "<i>Single-use, expires in 30 days. Give this to whoever should redeem it - "
        "they enter it on the Billing page of the website.</i>"
    )
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": [_back_row("« Codes", "adm:codes")]})


async def _send_users(client: httpx.AsyncClient, chat_id, message_id, page: int = 0) -> None:
    db = SessionLocal()
    try:
        total = db.query(User).count()
        users = (
            db.query(User).order_by(User.created_at.desc())
            .offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()
        )
        rows = [
            [{
                "text": f"{'🚫' if u.is_blocked else '✅'} {u.email} ({u.plan})",
                "callback_data": f"adm:u:{u.id}:{page}",
            }]
            for u in users
        ]
    finally:
        db.close()
    total_pages = max(1, -(-total // PAGE_SIZE))
    has_next = (page + 1) * PAGE_SIZE < total
    nav = _page_nav_row("adm:users", page, has_next)
    if nav:
        rows.append(nav)
    rows.append(_back_row())
    text = f"<b>👥 Users</b> — <i>page {page + 1} of {total_pages}</i> ({total} total)\n\nTap a user to manage."
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": rows})


async def _send_user_detail(client: httpx.AsyncClient, chat_id, message_id, user_id: int, page: int = 0) -> None:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if not u:
            await _reply(client, chat_id, message_id, "User not found.", {"inline_keyboard": [_back_row("« Users", f"adm:users:{page}")]})
            return
        text = (
            f"<b>👤 {_esc(u.first_name)} {_esc(u.last_name)}</b>\n"
            f"<code>{_esc(u.email)}</code>\n\n"
            f"<b>Plan:</b> {_esc(u.plan)} · <b>Apps:</b> {len(u.apps)}\n"
            f"<b>Blocked:</b> {'yes' if u.is_blocked else 'no'} · <b>Verified:</b> {'yes' if u.is_verified else 'no'}"
        )
        block_btn = (
            {"text": "✅ Unblock", "callback_data": f"adm:uunblk:{u.id}:{page}"}
            if u.is_blocked
            else {"text": "🚫 Block", "callback_data": f"adm:ublk:{u.id}:{page}"}
        )
    finally:
        db.close()
    rows = [
        [block_btn],
        [{"text": "🗑 Delete account", "callback_data": f"adm:udelc:{user_id}:{page}"}],
        _back_row("« Back to users", f"adm:users:{page}"),
    ]
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": rows})


async def _set_user_blocked(client: httpx.AsyncClient, chat_id, message_id, user_id: int, blocked: bool, page: int) -> None:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if u:
            u.is_blocked = blocked
            db.commit()
    finally:
        db.close()
    await _send_user_detail(client, chat_id, message_id, user_id, page)


async def _delete_user(client: httpx.AsyncClient, chat_id, message_id, user_id: int, page: int) -> None:
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
    await _reply(client, chat_id, message_id, "🗑 Account deleted.", {"inline_keyboard": [_back_row("« Users", f"adm:users:{page}")]})


async def _send_apps(client: httpx.AsyncClient, chat_id, message_id, page: int = 0) -> None:
    db = SessionLocal()
    try:
        total = db.query(BotApp).count()
        apps = (
            db.query(BotApp).order_by(BotApp.created_at.desc())
            .offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()
        )
        rows = [
            [{
                "text": f"{'⏸' if a.admin_suspended else '▶' if a.status == AppStatus.RUNNING else '⏹'} {a.name}",
                "callback_data": f"adm:a:{a.id}:{page}",
            }]
            for a in apps
        ]
    finally:
        db.close()
    total_pages = max(1, -(-total // PAGE_SIZE))
    has_next = (page + 1) * PAGE_SIZE < total
    nav = _page_nav_row("adm:apps", page, has_next)
    if nav:
        rows.append(nav)
    rows.append(_back_row())
    text = f"<b>📦 Apps</b> — <i>page {page + 1} of {total_pages}</i> ({total} total)\n\nTap an app to manage."
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": rows})


async def _send_app_detail(client: httpx.AsyncClient, chat_id, message_id, app_id: int, page: int = 0) -> None:
    db = SessionLocal()
    try:
        a = db.get(BotApp, app_id)
        if not a:
            await _reply(client, chat_id, message_id, "App not found.", {"inline_keyboard": [_back_row("« Apps", f"adm:apps:{page}")]})
            return
        text = (
            f"<b>📦 {_esc(a.name)}</b>\n"
            f"<b>Owner:</b> {_esc(a.owner.email)} ({_esc(a.owner.plan)})\n"
            f"<b>Status:</b> {_esc(a.status.value)}\n"
        )
        if a.admin_suspended:
            text += f"<b>Suspended:</b> <i>{_esc(a.admin_suspend_reason or 'yes')}</i>\n"
        text += f"<b>Bot:</b> @{_esc(a.telegram_username)}" if a.telegram_username else "<i>No Telegram bot detected</i>"
        suspend_btn = (
            {"text": "▶️ Unsuspend", "callback_data": f"adm:aunsusp:{a.id}:{page}"}
            if a.admin_suspended
            else {"text": "⏸ Suspend", "callback_data": f"adm:asusp:{a.id}:{page}"}
        )
    finally:
        db.close()
    rows = [
        [suspend_btn],
        [{"text": "🗑 Delete app", "callback_data": f"adm:adelc:{app_id}:{page}"}],
        _back_row("« Back to apps", f"adm:apps:{page}"),
    ]
    await _reply(client, chat_id, message_id, text, {"inline_keyboard": rows})


async def _set_app_suspended(client: httpx.AsyncClient, chat_id, message_id, app_id: int, suspended: bool, page: int) -> None:
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
    await _send_app_detail(client, chat_id, message_id, app_id, page)


async def _delete_app(client: httpx.AsyncClient, chat_id, message_id, app_id: int, page: int) -> None:
    db = SessionLocal()
    try:
        a = db.get(BotApp, app_id)
        if a:
            deploy_service.teardown_app(a)
            db.delete(a)
            db.commit()
    finally:
        db.close()
    await _reply(client, chat_id, message_id, "🗑 App deleted.", {"inline_keyboard": [_back_row("« Apps", f"adm:apps:{page}")]})


async def _handle_admin_callback(client: httpx.AsyncClient, query: dict) -> None:
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    data = query.get("data", "")
    await _call(client, "answerCallbackQuery", callback_query_id=query["id"])
    if not _is_admin(chat_id):
        return

    parts = data.split(":")
    # פורמטים: adm:menu | adm:stats | adm:codes | adm:gencode:<plan>
    #           adm:users:<page> | adm:apps:<page>
    #           adm:u:<id>:<page> | adm:ublk:<id>:<page> | adm:uunblk:<id>:<page>
    #           adm:udelc:<id>:<page> | adm:udel:<id>:<page>
    #           adm:a:<id>:<page> | adm:asusp:<id>:<page> | adm:aunsusp:<id>:<page>
    #           adm:adelc:<id>:<page> | adm:adel:<id>:<page>
    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        await _reply(client, chat_id, message_id, "<b>🛠 TeleBoss Admin</b>", ADMIN_MAIN_MENU)
    elif action == "stats":
        await _send_stats(client, chat_id, message_id)
    elif action == "codes":
        await _send_code_menu(client, chat_id, message_id)
    elif action == "gencode":
        await _generate_code(client, chat_id, message_id, parts[2])
    elif action == "users":
        await _send_users(client, chat_id, message_id, int(parts[2]))
    elif action == "apps":
        await _send_apps(client, chat_id, message_id, int(parts[2]))
    elif action == "u":
        await _send_user_detail(client, chat_id, message_id, int(parts[2]), int(parts[3]))
    elif action == "ublk":
        await _set_user_blocked(client, chat_id, message_id, int(parts[2]), True, int(parts[3]))
    elif action == "uunblk":
        await _set_user_blocked(client, chat_id, message_id, int(parts[2]), False, int(parts[3]))
    elif action == "udelc":
        uid, page = int(parts[2]), int(parts[3])
        rows = [[
            {"text": "⚠️ Yes, delete", "callback_data": f"adm:udel:{uid}:{page}"},
            {"text": "Cancel", "callback_data": f"adm:u:{uid}:{page}"},
        ]]
        await _reply(
            client, chat_id, message_id,
            "<b>⚠️ Delete this account and ALL its apps?</b>\nThis cannot be undone.",
            {"inline_keyboard": rows},
        )
    elif action == "udel":
        await _delete_user(client, chat_id, message_id, int(parts[2]), int(parts[3]))
    elif action == "a":
        await _send_app_detail(client, chat_id, message_id, int(parts[2]), int(parts[3]))
    elif action == "asusp":
        await _set_app_suspended(client, chat_id, message_id, int(parts[2]), True, int(parts[3]))
    elif action == "aunsusp":
        await _set_app_suspended(client, chat_id, message_id, int(parts[2]), False, int(parts[3]))
    elif action == "adelc":
        aid, page = int(parts[2]), int(parts[3])
        rows = [[
            {"text": "⚠️ Yes, delete", "callback_data": f"adm:adel:{aid}:{page}"},
            {"text": "Cancel", "callback_data": f"adm:a:{aid}:{page}"},
        ]]
        await _reply(client, chat_id, message_id, "<b>⚠️ Delete this app permanently?</b>", {"inline_keyboard": rows})
    elif action == "adel":
        await _delete_app(client, chat_id, message_id, int(parts[2]), int(parts[3]))


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
