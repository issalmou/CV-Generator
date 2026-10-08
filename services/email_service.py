"""
Service: EmailService

One public method — ``send_reset_code`` — with a three-tier delivery chain
chosen at call time:

1. ``RESEND_API_KEY`` set  -> Resend HTTP API (POST https://api.resend.com/emails, via httpx)
2. else ``SMTP_HOST`` set  -> plain SMTP over STARTTLS (stdlib smtplib)
3. else                    -> dev mode: log the code, deliver nothing

It never raises: a delivery failure is logged and swallowed so
``POST /api/auth/forgot-password`` still returns its uniform 200 (and never
leaks whether the address exists).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from config import settings

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_SUBJECT = "Your password reset code"
_REACTIVATION_SUBJECT = "Your account reactivation key"


def _body(code: str) -> str:
    return (
        "You (or someone using your e-mail) requested a password reset.\n\n"
        f"Your verification code is: {code}\n\n"
        f"It expires in {settings.PASSWORD_RESET_CODE_EXPIRE_MINUTES} minutes and can be "
        "used once. If you did not request this, you can safely ignore this message."
    )


def _reactivation_body(code: str) -> str:
    return (
        "You (or someone using your e-mail) asked to reactivate a deactivated "
        "account.\n\n"
        f"Your reactivation key is: {code}\n\n"
        f"It expires in {settings.REACTIVATION_CODE_EXPIRE_MINUTES} minutes and can be "
        "used once. If you did not request this, you can safely ignore this message "
        "and your account will stay deactivated."
    )


class EmailService:
    def send_reset_code(self, to_email: str, code: str) -> None:
        self._deliver(to_email, _SUBJECT, _body(code), "reset code")

    def send_reactivation_code(self, to_email: str, code: str) -> None:
        self._deliver(
            to_email, _REACTIVATION_SUBJECT, _reactivation_body(code), "reactivation key"
        )

    # ------------------------------------------------------------------

    def _deliver(self, to_email: str, subject: str, text: str, what: str) -> None:
        try:
            if settings.RESEND_API_KEY:
                self._send_via_resend(to_email, subject, text)
            elif settings.SMTP_HOST:
                self._send_via_smtp(to_email, subject, text)
            else:
                logger.info("[EmailService] (dev mode) %s for %s: %s", what, to_email, text)
        except Exception as exc:  # noqa: BLE001 - delivery is best-effort
            logger.warning("[EmailService] Failed to send %s to %s: %s", what, to_email, exc)

    @staticmethod
    def _send_via_resend(to_email: str, subject: str, text: str) -> None:
        resp = httpx.post(
            _RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={
                "from": settings.EMAIL_FROM,
                "to": [to_email],
                "subject": subject,
                "text": text,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        logger.info("[EmailService] Sent '%s' via Resend to %s", subject, to_email)

    @staticmethod
    def _send_via_smtp(to_email: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10.0) as server:
            server.starttls()
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("[EmailService] Sent '%s' via SMTP to %s", subject, to_email)
