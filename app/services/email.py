import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("serves.email")


class InvalidRecipientError(Exception):
    """הכתובת נדחתה ע"י שרת ה-SMTP עצמו (למשל תיבת דואר שלא קיימת) -
    בשונה מכשל תצורה/רשת כללי, זו שגיאה שכדאי להציג למשתמש ישירות."""


def _html_body(title: str, intro: str, code: str) -> str:
    return f"""\
<body style="margin:0;padding:32px 16px;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center">
      <table role="presentation" width="420" cellpadding="0" cellspacing="0" style="max-width:420px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e6e6e6;">
        <tr><td style="padding:28px 32px 4px;">
          <span style="font-size:22px;font-weight:800;color:#e0501c;letter-spacing:-0.02em;">Serves</span>
        </td></tr>
        <tr><td style="padding:8px 32px 0;">
          <h1 style="margin:0 0 8px;font-size:18px;color:#1a1a1a;">{title}</h1>
          <p style="margin:0 0 20px;font-size:14px;line-height:1.5;color:#555;">{intro}</p>
        </td></tr>
        <tr><td style="padding:0 32px 24px;">
          <div style="background:#faf2ee;border:1px solid #f0d9cc;border-radius:8px;padding:18px;text-align:center;">
            <span style="font-family:'SFMono-Regular',Consolas,monospace;font-size:32px;font-weight:700;letter-spacing:8px;color:#e0501c;">{code}</span>
          </div>
        </td></tr>
        <tr><td style="padding:0 32px 28px;">
          <p style="margin:0;font-size:12px;line-height:1.5;color:#999;">
            If you didn't request this, you can safely ignore this email.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>"""


def _send(to_email: str, subject: str, text_body: str, html_body: str) -> None:
    if not settings.SMTP_HOST:
        logger.warning("SMTP not configured - email to %s not sent (subject=%r)", to_email, subject)
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

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
    except smtplib.SMTPRecipientsRefused as exc:
        logger.warning("recipient refused for %s: %s", to_email, exc)
        raise InvalidRecipientError(str(exc)) from exc
    except smtplib.SMTPResponseException as exc:
        # חלק מהספקים דוחים תיבות לא קיימות עם קוד 550/553 בתגובה כללית
        # (לא דרך SMTPRecipientsRefused) - נזהה לפי קוד התגובה.
        if exc.smtp_code in (550, 551, 553):
            logger.warning("recipient rejected for %s: %s", to_email, exc)
            raise InvalidRecipientError(str(exc)) from exc
        logger.exception("Failed to send email to %s", to_email)
        raise
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        raise


def send_verification_email(to_email: str, code: str) -> None:
    subject = "Your Serves verification code"
    intro = f"Use the code below to finish signing up for Serves. It expires in {settings.VERIFICATION_CODE_TTL_MINUTES} minutes."
    text_body = (
        f"Your Serves verification code is: {code}\n\n"
        f"This code expires in {settings.VERIFICATION_CODE_TTL_MINUTES} minutes.\n"
        "If you didn't try to sign up for Serves, you can ignore this email."
    )
    _send(to_email, subject, text_body, _html_body("Verify your email", intro, code))


def send_password_reset_email(to_email: str, code: str) -> None:
    subject = "Your Serves password reset code"
    intro = f"Use the code below to reset your Serves password. It expires in {settings.VERIFICATION_CODE_TTL_MINUTES} minutes."
    text_body = (
        f"Your Serves password reset code is: {code}\n\n"
        f"This code expires in {settings.VERIFICATION_CODE_TTL_MINUTES} minutes.\n"
        "If you didn't request a password reset, you can ignore this email."
    )
    _send(to_email, subject, text_body, _html_body("Reset your password", intro, code))
