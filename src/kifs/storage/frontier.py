"""The user crawl frontier, on SQLite (issue #3, #4, #9).

v1 kept a ``crawled_users`` set and a ``user_queue`` list: crawled users were
never revisited, and the queue was silently truncated at 50,000. v2 fixed the
policy but kept the whole frontier in one JSON file, which reached 12.5MB and
had to be parsed and rewritten in full.

Now every user is a row with a ``next_crawl_at``. Picking the next user is one
indexed query — unseen users (``next_crawl_at IS NULL``) sort first, then
whoever is furthest overdue. Nothing is loaded up front and nothing is
discarded.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Optional

from kifs.storage.database import KifuDatabase, isoformat, utcnow

log = logging.getLogger(__name__)


class Frontier:
    def __init__(self, db: KifuDatabase, recrawl_hours: float = 24.0):
        """The frontier lives in the same SQLite file as the games.

        It shares the database's connection and transaction: SQLite permits one
        writer, so a second connection would only deadlock against the first.
        """
        self._db = db
        self.recrawl_hours = recrawl_hours

    # -- lifecycle ----------------------------------------------------
    def load(self) -> "Frontier":
        self._db.open()
        log.info("Frontier: %d known users, %d never crawled.",
                 len(self), self.never_crawled)
        return self

    @property
    def path(self) -> Path:
        return self._db.path

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db.connection

    def _begin(self) -> None:
        self._db.transaction.begin()

    def save(self, force: bool = False) -> bool:
        """Commit through the shared transaction (the database owns it)."""
        return self._db.transaction.commit(force=force)

    # -- membership ---------------------------------------------------
    def __contains__(self, user_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def __len__(self) -> int:
        return self.connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def add(self, user_id: str) -> bool:
        """Register a newly discovered user. Returns False if already known."""
        self._begin()
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, first_seen_at, last_crawled_at, next_crawl_at, games_found, crawls) "
            "VALUES (?, ?, NULL, NULL, 0, 0)",
            (user_id, isoformat(utcnow())),
        )
        if cursor.rowcount > 0:
            self._db.transaction.mark()
            return True
        return False

    def add_many(self, user_ids: Iterable[str]) -> int:
        return sum(1 for user_id in user_ids if self.add(user_id))

    # -- scheduling ---------------------------------------------------
    @property
    def never_crawled(self) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) AS n FROM users WHERE next_crawl_at IS NULL").fetchone()["n"]

    def due_count(self, now: Optional[datetime] = None) -> int:
        moment = now or utcnow()
        return self.connection.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE next_crawl_at IS NULL OR next_crawl_at <= ?",
            (isoformat(moment),),
        ).fetchone()["n"]

    #: How long a claimed user is held before it becomes selectable again.
    LEASE_MINUTES = 15.0

    def peek_user(self, now: Optional[datetime] = None) -> Optional[str]:
        """The next user to crawl, without claiming it. For status output."""
        moment = now or utcnow()
        row = self.connection.execute(
            "SELECT user_id FROM users "
            "WHERE next_crawl_at IS NULL OR next_crawl_at <= ? "
            "ORDER BY next_crawl_at ASC, first_seen_at ASC LIMIT 1",
            (isoformat(moment),),
        ).fetchone()
        return row["user_id"] if row else None

    def claim_user(self, now: Optional[datetime] = None,
                   lease_minutes: Optional[float] = None) -> Optional[str]:
        """Take the next user to crawl and put a short lease on it.

        Unseen users first (``next_crawl_at IS NULL`` sorts first in SQLite),
        then whoever is furthest overdue; ``idx_users_next`` serves the query.

        The lease matters: selection is now a query rather than a pop, so
        without it a user whose crawl raised would be selected again on the
        very next cycle, forever. With it, a failed or interrupted crawl simply
        comes back in ``LEASE_MINUTES`` — nothing is lost, nothing spins.
        :meth:`mark_crawled` then replaces the lease with the real interval.
        """
        moment = now or utcnow()
        user_id = self.peek_user(moment)
        if user_id is None:
            return None
        lease = lease_minutes if lease_minutes is not None else self.LEASE_MINUTES
        self._begin()
        self.connection.execute(
            "UPDATE users SET next_crawl_at = ? WHERE user_id = ?",
            (isoformat(moment + timedelta(minutes=lease)), user_id),
        )
        self._db.transaction.mark()
        return user_id

    def next_user(self, now: Optional[datetime] = None) -> Optional[str]:
        """Backwards-compatible alias for :meth:`claim_user`."""
        return self.claim_user(now)

    def mark_crawled(self, user_id: str, games_found: int,
                     recrawl_hours: Optional[float] = None) -> None:
        now = utcnow()
        interval = recrawl_hours if recrawl_hours is not None else self.recrawl_hours
        self._begin()
        self.connection.execute(
            "INSERT INTO users (user_id, first_seen_at, last_crawled_at, "
            "                   next_crawl_at, games_found, crawls) "
            "VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  last_crawled_at = excluded.last_crawled_at, "
            "  next_crawl_at   = excluded.next_crawl_at, "
            "  games_found     = users.games_found + excluded.games_found, "
            "  crawls          = users.crawls + 1",
            (user_id, isoformat(now), isoformat(now),
             isoformat(now + timedelta(hours=interval)), games_found),
        )
        self._db.transaction.mark()

    def get(self, user_id: str) -> Optional[dict]:
        row = self.connection.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return {key: row[key] for key in row.keys()} if row else None

    def stats(self) -> Dict[str, int]:
        return {
            "users_known": len(self),
            "users_never_crawled": self.never_crawled,
            "users_due": self.due_count(),
        }
