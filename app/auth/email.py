import logging
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings

logger = logging.getLogger(__name__)


def build_password_reset_url(token: str, base_url: str) -> str:
    base_url = base_url.rstrip("/")
    return f"{base_url}/reset-password?{urlencode({'token': token})}"


async def send_password_reset_email(
    email: str,
    token: str,
    config: dict[str, Any],
) -> bool:
    """Send the reset email using effective settings.

    *config* is the effective settings dict (DB overrides merged over .env).
    SMTP credentials (username/password) are intentionally read from .env only.
    """
    if not config["smtp_host"]:
        logger.error("Password reset email skipped because SMTP host is not configured")
        return False

    import aiosmtplib

    reset_url = build_password_reset_url(token, config["frontend_base_url"])
    message = EmailMessage()
    message["From"] = config["smtp_from_email"]
    message["To"] = email
    message["Subject"] = "Reset your QA Screenshot Tool password"
    message.set_content(
        "\n".join(
            [
                "We received a request to reset your password.",
                "",
                "Open this link to choose a new password:",
                reset_url,
                "",
                (
                    "This link expires in "
                    f"{config['password_reset_token_expire_minutes']} minutes. "
                    "If you did not request it, ignore this email."
                ),
            ]
        )
    )

    await aiosmtplib.send(
        message,
        hostname=config["smtp_host"],
        port=config["smtp_port"],
        username=settings.SMTP_USERNAME,
        password=settings.SMTP_PASSWORD,
        start_tls=config["smtp_use_tls"],
    )
    return True
