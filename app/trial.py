import datetime

from app.config import settings
from app.models import User


def trial_days_remaining(user: User) -> int | None:
    """ימים שנותרו לתקופת הניסיון של תוכנית חינמית. None אם לא רלוונטי
    (המשתמש לא בתוכנית חינמית, או שאין הגבלת ניסיון בכלל - FREE_TRIAL_DAYS=0)."""
    if user.plan != "free" or settings.FREE_TRIAL_DAYS <= 0 or not user.created_at:
        return None
    created = user.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    elapsed_days = (datetime.datetime.now(datetime.timezone.utc) - created).days
    return settings.FREE_TRIAL_DAYS - elapsed_days


def is_trial_expired(user: User) -> bool:
    remaining = trial_days_remaining(user)
    return remaining is not None and remaining <= 0
