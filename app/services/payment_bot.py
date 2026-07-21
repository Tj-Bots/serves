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

from app.config import settings
from app.database import SessionLocal
from app.models import PlanPurchase, PurchaseStatus, User

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


async def _dispatch(client: httpx.AsyncClient, update: dict) -> None:
    message = update.get("message")
    if message and "successful_payment" in message:
        await _handle_successful_payment(client, message)
    elif message and message.get("text", "").startswith("/start"):
        await _handle_start(client, message)
    elif "pre_checkout_query" in update:
        await _handle_pre_checkout(client, update["pre_checkout_query"])


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
