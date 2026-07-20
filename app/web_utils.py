from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def flash(request: Request, message: str, category: str = "info") -> None:
    flashes = request.session.get("_flashes", [])
    flashes.append({"message": message, "category": category})
    request.session["_flashes"] = flashes


def render(request: Request, name: str, status_code: int = 200, **context):
    flashes = request.session.pop("_flashes", [])
    context.update(
        request=request,
        flashes=flashes,
        terms_url=settings.TERMS_URL,
        user=context.get("user"),
    )
    return templates.TemplateResponse(name, context, status_code=status_code)
