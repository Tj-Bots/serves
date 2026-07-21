"""
מגביל קצב פשוט בזיכרון (sliding window), לא תלוי ב-Redis - מספיק לתהליך
uvicorn יחיד כמו שהפלטפורמה רצה כרגע. לא שורד restart, וזה בסדר - זו
הגנה נגד ניצול לרעה, לא בקרת גישה קריטית לביטחון."""

import threading
import time

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def check(key: str, max_hits: int, window_seconds: float) -> bool:
    """True אם הפעולה מותרת (ונרשמת), False אם חרגה מהמגבלה."""
    now = time.time()
    with _lock:
        hits = [t for t in _hits.get(key, []) if now - t < window_seconds]
        if len(hits) >= max_hits:
            _hits[key] = hits
            return False
        hits.append(now)
        _hits[key] = hits
        return True
