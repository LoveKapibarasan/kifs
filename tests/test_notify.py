from datetime import datetime, timedelta, timezone

import pytest

from kifs.notify.alerts import ALERT_STALLED, Alerter
from kifs.notify.mailer import MailError, Mailer
from kifs.notify.report import build_report, render_html, render_text
from kifs.storage.database import KifuDatabase


class FakeMailer:
    """Stands in for SMTP; records what would have gone out."""

    def __init__(self, configured=True, fail=False):
        self.configured = configured
        self.fail = fail
        self.sent = []

    def send(self, subject, text_body, html_body=None, to=None):
        if self.fail:
            raise MailError("connection refused")
        self.sent.append({"subject": subject, "text": text_body, "to": to})


@pytest.fixture
def mail_settings(settings):
    settings.smtp_server = "mail.example.org"
    settings.smtp_port = 587
    settings.smtp_username = "postmaster@example.org"
    settings.smtp_password = "secret"
    settings.smtp_from = "postmaster@example.org"
    settings.report_to = "someone@example.org"
    return settings


# -- alerts -----------------------------------------------------------
def test_alert_is_sent_once_then_suppressed(mail_settings):
    mailer = FakeMailer()
    alerter = Alerter(mail_settings, mailer)
    assert alerter.fire("k", "subject", "body") is True
    assert alerter.fire("k", "subject", "body") is False, "cooldown must suppress"
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["subject"] == "[kifs] subject"


def test_alert_fires_again_after_the_cooldown(mail_settings):
    mail_settings.alert_cooldown_hours = 6.0
    mailer = FakeMailer()
    alerter = Alerter(mail_settings, mailer)
    alerter.fire("k", "s", "b")
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    assert alerter.fire("k", "s", "b", now=later) is True


def test_clearing_a_condition_lets_it_alert_immediately(mail_settings):
    mailer = FakeMailer()
    alerter = Alerter(mail_settings, mailer)
    alerter.fire(ALERT_STALLED, "s", "b")
    alerter.clear(ALERT_STALLED)
    assert alerter.fire(ALERT_STALLED, "s", "b") is True


def test_cooldown_survives_a_restart(mail_settings):
    """A crash loop must not turn one problem into one email per restart."""
    mailer = FakeMailer()
    Alerter(mail_settings, mailer).fire("k", "s", "b")
    assert Alerter(mail_settings, FakeMailer()).fire("k", "s", "b") is False


def test_send_failure_does_not_raise(mail_settings):
    """A broken mail server must never take the collector down."""
    alerter = Alerter(mail_settings, FakeMailer(fail=True))
    assert alerter.fire("k", "s", "b") is False


def test_alerts_can_be_disabled(mail_settings):
    mail_settings.alerts_enabled = False
    mailer = FakeMailer()
    assert Alerter(mail_settings, mailer).fire("k", "s", "b") is False
    assert mailer.sent == []


# -- report -----------------------------------------------------------
def _seed(settings, count: int) -> None:
    db = KifuDatabase(settings.db_path, flush_every_writes=10_000).open()
    for index in range(count):
        game_id = f"a-b-2026010{index % 9}_00000{index % 9}-{index}"
        db.upsert_game({"game_id": game_id, "sente": "a", "gote": "b", "total_moves": 50})
        db.add_record(game_id, "sb", "a")
        db.mark_indexed(game_id)
    db.close()


def test_report_reports_a_delta_against_the_previous_run(settings):
    _seed(settings, 3)
    first = build_report(settings)
    assert first["current"]["games_indexed"] == 3
    assert first["delta"] == {}, "the first report has no baseline"

    _seed(settings, 5)
    second = build_report(settings)
    assert second["current"]["games_indexed"] == 5
    assert second["delta"]["games_indexed"] == 2


def test_dry_run_does_not_move_the_baseline(settings):
    _seed(settings, 2)
    build_report(settings, persist=True)
    _seed(settings, 4)
    build_report(settings, persist=False)          # a preview
    again = build_report(settings, persist=True)
    assert again["delta"]["games_indexed"] == 2, "the preview must not consume the delta"


def test_zero_progress_raises_a_warning(settings):
    _seed(settings, 2)
    build_report(settings)
    report = build_report(settings)
    assert report["delta"]["games_indexed"] == 0
    assert any("0件" in warning for warning in report["warnings"])


def test_renderers_produce_output(settings):
    _seed(settings, 2)
    report = build_report(settings)
    text = render_text(report)
    html = render_html(report)
    assert "索引済み対局" in text and "2" in text
    assert html.startswith("<!doctype html>") and "棋譜収集 日次レポート" in html


# -- mailer -----------------------------------------------------------
def test_mailer_refuses_when_unconfigured(settings):
    with pytest.raises(MailError, match="not configured"):
        Mailer(settings).send("s", "b")


def test_mail_configured_requires_every_field(mail_settings):
    assert Mailer(mail_settings).configured is True
    mail_settings.report_to = None
    assert Mailer(mail_settings).configured is False
