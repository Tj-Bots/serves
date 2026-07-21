import logging
import smtplib
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("serves.email")


def send_verification_email(to_email: str, code: str) -> None:
    subject = "קוד האימות שלך ל-Serves"
    body = (
        f"קוד האימות שלך הוא: {code}\n\n"
        f"הקוד תקף ל-{settings.VERIFICATION_CODE_TTL_MINUTES} דקות.\n"
        "אם לא ניסית להירשם ל-Serves, אפשר להתעלם מהמייל הזה."
    )

    if not settings.SMTP_HOST:
        logger.warning("SMTP not configured - verification code for %s is: %s", to_email, code)
        return

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email

    try:
        if settings.SMTP_USE_SSL:
            server_cm = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        else:
            server_cm = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        with server_cm as server:
            if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
                server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, [to_email], message.as_string())
    except Exception:
        logger.exception("Failed to send verification email to %s (code=%s)", to_email, code)
        raise
