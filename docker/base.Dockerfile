# תמונת בסיס משותפת לכל בוטי הטלגרם שרצים על הפלטפורמה.
# משתמש לא-root קבוע (botuser, uid 1000) - אין sudo/apt בתוך הקונטיינר,
# כך שפיזית אי אפשר להתקין ffmpeg או כל חבילת מערכת אחרת. ניתן להתקין
# רק ספריות פייתון דרך pip (לתוך /app/.local, כי שאר מערכת הקבצים read-only).

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash botuser

ENV PYTHONUSERBASE=/app/.local \
    PATH="/app/.local/bin:${PATH}" \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER botuser
WORKDIR /app

ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
