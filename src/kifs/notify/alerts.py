"""Immediate alerts for conditions that need a human (issue-driven, not scheduled).

Two rules keep this from becoming noise:

* every alert has a key, and the same key is not re-sent within
  ``KIFS_ALERT_COOLDOWN_HOURS``;
* the cooldown is stored in ``data/state/alerts.json``, so a restart loop cannot
  turn one problem into one email per restart.

A resolved condition clears its key, so the next occurrence alerts immediately.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import orjson

from kifs.config import Settings
from kifs.notify.mailer import Mailer, MailError
from kifs.storage.database import parse_iso

log = logging.getLogger(__name__)

ALERT_COOKIE_EXPIRED = "cookie_expired"
ALERT_STALLED = "collection_stalled"
ALERT_CYCLE_FAILING = "cycle_failing"


class Alerter:
    def __init__(self, settings: Settings, mailer: Optional[Mailer] = None):
        self._settings = settings
        self._mailer = mailer or Mailer(settings)
        self._state: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        path = self._settings.alert_state_path
        if path.is_file() and path.stat().st_size > 0:
            try:
                self._state = orjson.loads(path.read_bytes())
            except ValueError:
                self._state = {}

    def _save(self) -> None:
        path = self._settings.alert_state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as handle:
            handle.write(orjson.dumps(self._state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def _in_cooldown(self, key: str, now: datetime) -> bool:
        last = parse_iso(self._state.get(key))
        if last is None:
            return False
        return now - last < timedelta(hours=self._settings.alert_cooldown_hours)

    def clear(self, key: str) -> None:
        """Mark a condition as resolved so its next occurrence alerts at once."""
        if key in self._state:
            del self._state[key]
            self._save()

    def fire(self, key: str, subject: str, body: str,
             now: Optional[datetime] = None) -> bool:
        """Send an alert unless the same key is still in its cooldown."""
        moment = now or datetime.now(timezone.utc)
        if not self._settings.alerts_enabled:
            return False
        if self._in_cooldown(key, moment):
            log.debug("Alert '%s' suppressed (cooldown).", key)
            return False
        if not self._mailer.configured:
            log.warning("Alert '%s' not sent: SMTP is not configured. %s", key, subject)
            return False

        text = (f"{body}\n\n"
                f"発生時刻: {moment.isoformat()}\n"
                f"ホスト  : kifs collector (172.25.20.20)\n"
                f"確認    : journalctl --user -u kifs-collector -n 50\n")
        try:
            self._mailer.send(f"[kifs] {subject}", text)
        except MailError as exc:
            # A failed alert must never take the collector down with it.
            log.error("Could not send alert '%s': %s", key, exc)
            return False

        self._state[key] = moment.isoformat()
        self._save()
        return True
