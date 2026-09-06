"""SMTP delivery.

Credentials come from Infisical alongside the session cookies (``SMTP_SERVER``,
``SMTP_PORT``, ``SMTP_USERNAME``, ``SMTP_PASSWORD``, ``SMTP_FROM``,
``REPORT_TO``), so nothing mail-related lives in the repository.

Note that the submission ports on the mail host are only reachable from the
collector host, not from every machine in the office — ``kifs report --dry-run``
exists so the report can still be checked anywhere.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Optional

from kifs.config import Settings

log = logging.getLogger(__name__)


class MailError(RuntimeError):
    """Delivery failed. Never fatal for the collector — it keeps crawling."""


class Mailer:
    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.mail_configured

    def send(self, subject: str, text_body: str,
             html_body: Optional[str] = None,
             to: Optional[str] = None) -> None:
        settings = self._settings
        if not settings.mail_configured:
            raise MailError(
                "SMTP is not configured; expected SMTP_SERVER / SMTP_USERNAME / "
                "SMTP_PASSWORD / REPORT_TO from Infisical."
            )

        recipient = to or settings.report_to
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr(("kifs collector", settings.smtp_from))
        message["To"] = recipient
        message["Date"] = formatdate(localtime=True)
        # Lets the mail client thread the daily reports together.
        message["X-Kifs-Report"] = "1"
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        try:
            context = ssl.create_default_context()
            if settings.smtp_port == 465:
                with smtplib.SMTP_SSL(settings.smtp_server, settings.smtp_port,
                                      timeout=30, context=context) as server:
                    server.login(settings.smtp_username, settings.smtp_password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(settings.smtp_server, settings.smtp_port,
                                  timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(settings.smtp_username, settings.smtp_password)
                    server.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise MailError(f"{type(exc).__name__}: {exc}") from exc

        log.info("Sent '%s' to %s via %s:%d.", subject, recipient,
                 settings.smtp_server, settings.smtp_port)
