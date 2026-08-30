from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.i18n import get_lang, t

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["thousands"] = lambda n: f"{n:,}"


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
