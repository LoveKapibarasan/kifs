"""Email delivery: the daily report and immediate alerts."""

from kifs.notify.alerts import Alerter
from kifs.notify.mailer import Mailer, MailError
from kifs.notify.report import build_report, render_html, render_text

__all__ = ["Alerter", "Mailer", "MailError", "build_report", "render_html", "render_text"]
