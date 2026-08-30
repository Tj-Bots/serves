from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.i18n import get_lang, t

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["thousands"] = lambda n: f"{n:,}"


def static_url(rel_path: str) -> str:
    """מוסיף ?v=<mtime> לקובץ סטטי (css/js) לפי זמן השינוי שלו בדיסק, כדי
    שדפדפנים יטענו את הגרסה החדשה מייד אחרי דיפלוי במקום להציג גרסה
    ישנה ש-cache-י (בלי query param קבוע, שינוי בקובץ לא היה משנה את
    ה-URL בעיני הדפדפן, אז cache אגרסיבי היה יכול לתקוע גרסה ישנה)."""
    file_path = STATIC_DIR / rel_path.lstrip("/")
    try:
        version = int(file_path.stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{rel_path}?v={version}"


templates.env.globals["static_url"] = static_url


def flash(request: Request, key: str, category: str = "info", **kwargs) -> None:
    flashes = request.session.get("_flashes", [])
    flashes.append({"key": key, "category": category, "kwargs": kwargs})
    request.session["_flashes"] = flashes


def render(request: Request, name: str, status_code: int = 200, **context):
    lang = get_lang(request)
    raw_flashes = request.session.pop("_flashes", [])
    flashes = [
        {"message": t(lang, f["key"], **f["kwargs"]), "category": f["category"]}
        for f in raw_flashes
    ]
    context.update(
        request=request,
        flashes=flashes,
        terms_url="/terms",
        user=context.get("user"),
        lang=lang,
        dir=("rtl" if lang == "he" else "ltr"),
        t=lambda key, **kw: t(lang, key, **kw),
    )
    return templates.TemplateResponse(name, context, status_code=status_code)
