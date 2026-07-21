import datetime
import secrets

from sqlalchemy.orm import Session

from app.models import PromoCode, User

# בלי 0/O/1/I כדי למנוע בלבול כשמעתיקים קוד ביד
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
PROMO_CODE_TTL_DAYS = 30


def generate_code_string() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def create_promo_code(db: Session, plan_name: str, created_by_telegram_id: int) -> PromoCode:
    code = PromoCode(
        code=generate_code_string(),
        plan_name=plan_name,
        created_by_telegram_id=created_by_telegram_id,
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=PROMO_CODE_TTL_DAYS),
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def redeem_promo_code(db: Session, raw_code: str, user: User) -> str | None:
    """אם הקוד תקף, מעניק את התוכנית שלו ל-user ומחזיר None (הצלחה).
    אחרת מחזיר מפתח תרגום שמסביר למה זה נכשל."""
    code_str = raw_code.strip().upper()
    if not code_str:
        return "billing.flash.redeem_empty"

    promo = db.query(PromoCode).filter(PromoCode.code == code_str).first()
    if not promo:
        return "billing.flash.redeem_invalid"
    if promo.used_count >= promo.max_uses:
        return "billing.flash.redeem_used"
    if promo.expires_at:
        expires_at = promo.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) > expires_at:
            return "billing.flash.redeem_expired"

    promo.used_count += 1
    user.plan = promo.plan_name
    db.commit()
    return None
