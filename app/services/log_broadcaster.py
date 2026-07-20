"""
Pub/sub פשוט בזיכרון להזרמת לוגים בזמן אמת ללקוחות WebSocket מחוברים,
בנוסף לכתיבה מתמשכת לקובץ על הדיסק (מקור האמת להיסטוריה).
"""

import asyncio
import collections

from app.config import settings

_subscribers: dict[int, set[asyncio.Queue]] = collections.defaultdict(set)
_locks: dict[int, asyncio.Lock] = collections.defaultdict(asyncio.Lock)


def log_path(app_id: int) -> str:
    return str(settings.LOGS_DIR / f"{app_id}.log")


async def append_line(app_id: int, line: str) -> None:
    async with _locks[app_id]:
        with open(log_path(app_id), "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    for queue in list(_subscribers[app_id]):
        queue.put_nowait(line)


def read_tail(app_id: int, max_lines: int = 500) -> list[str]:
    try:
        with open(log_path(app_id), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    return [line.rstrip("\n") for line in lines[-max_lines:]]


def clear_log(app_id: int) -> None:
    try:
        import os

        os.remove(log_path(app_id))
    except FileNotFoundError:
        pass


def subscribe(app_id: int) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[app_id].add(queue)
    return queue


def unsubscribe(app_id: int, queue: asyncio.Queue) -> None:
    _subscribers[app_id].discard(queue)
