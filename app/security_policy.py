"""
מדיניות אבטחה לפלטפורמה: חסימת ספריות/פקודות שמאפשרות עקיפת המדיניות
(הורדת טורנטים, שימוש ב-ffmpeg, קבלת הרשאות מוגברות בתוך הקונטיינר).

זו שכבת הגנה *נוספת* על הבידוד עצמו (קונטיינר ללא root, ללא apt/sudo,
ו-iptables שחוסם פורטי BitTorrent ברמת הרשת). גם בלי השכבה הזו המשתמש
לא יכול להתקין ffmpeg כי אין לו הרשאות מערכת - אבל היא תופסת ניסיונות
מוקדם יותר, עם הודעת שגיאה ברורה, לפני שמבזבזים משאבי דיפלוי.
"""

import re

# חבילות pip שעוטפות/מורידות בינארי ffmpeg או משמשות ל-BitTorrent.
BLOCKED_PACKAGES = {
    "ffmpeg-python",
    "imageio-ffmpeg",
    "static-ffmpeg",
    "ffmpeg-downloader",
    "pyffmpeg",
    "libtorrent",
    "python-libtorrent",
    "qbittorrent-api",
    "transmissionrpc",
    "transmission-rpc",
    "deluge-client",
    "rtorrent-python",
}

# מילים/פקודות חשודות בתוך פקודת ההרצה - ניסיון לעקוף את חוסר ה-root.
BLOCKED_COMMAND_PATTERNS = [
    r"\bapt(-get)?\b",
    r"\bsudo\b",
    r"\bdpkg\b",
    r"\bsnap\s+install\b",
    r"\byum\b",
    r"\bmagnet:",
    r"\.torrent\b",
]


class PolicyViolation(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _normalize(pkg_name: str) -> str:
    return pkg_name.strip().lower().replace("_", "-")


def parse_requirement_names(requirements_text: str) -> list[str]:
    names = []
    for raw_line in requirements_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # חתוך סימני גרסה/extras: package[extra]==1.2.3 , package>=1.0
        match = re.match(r"^([A-Za-z0-9._-]+)", line)
        if match:
            names.append(_normalize(match.group(1)))
    return names


def check_requirements(requirements_text: str) -> None:
    names = parse_requirement_names(requirements_text)
    blocked = [n for n in names if n in BLOCKED_PACKAGES]
    if blocked:
        raise PolicyViolation(
            "לא ניתן לפרוס: הספריות הבאות אסורות לשימוש בתוכנית זו (ffmpeg/torrent): "
            + ", ".join(sorted(set(blocked)))
        )


def check_run_command(run_command: str) -> None:
    for pattern in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, run_command, re.IGNORECASE):
            raise PolicyViolation(
                "פקודת ההרצה מכילה ביטוי חסום (התקנת חבילות מערכת/הרשאות מוגברות/טורנט אסורים)."
            )
