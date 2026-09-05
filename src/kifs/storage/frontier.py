"""The user crawl frontier (issue #3, issue #4).

The v1 pipeline kept a ``crawled_users`` set and a ``user_queue`` list. Once a
user landed in the set they were never visited again, so no new game by a known
player was ever collected; and the queue was silently truncated at 50,000
entries, discarding users with no record that it happened.

Here every user is a row with a ``next_crawl_at``, so the frontier is a
never-ending rotation: unseen users first, then whoever is due for a revisit.
Nothing is discarded.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional

import orjson

from kifs.storage.database import isoformat, parse_iso, utcnow

log = logging.getLogger(__name__)


class Frontier:
    def __init__(self, path: Path, recrawl_hours: float = 24.0,
                 flush_every_seconds: float = 60.0):
        self.path = Path(path)
        self.recrawl_hours = recrawl_hours
        self.flush_every_seconds = flush_every_seconds
        #: user_id -> {first_seen_at, last_crawled_at, next_crawl_at, games_found, crawls}
        self.users: Dict[str, dict] = {}
        #: Users never crawled, in discovery order.
        self._pending: Deque[str] = deque()
        self._pending_set: set[str] = set()
        #: Users due for a revisit, rebuilt lazily by :meth:`_rebuild_due_queue`.
        self._due_queue: Deque[str] = deque()
        self.seed_cursor = 0
        self._dirty = False
        self._last_flush = time.monotonic()

    # -- persistence --------------------------------------------------
    def load(self) -> "Frontier":
        if self.path.is_file() and self.path.stat().st_size > 0:
            raw = orjson.loads(self.path.read_bytes())
            self.users = raw.get("users", {})
            pending = [u for u in raw.get("pending", []) if u in self.users]
            self._pending = deque(pending)
            self._pending_set = set(pending)
            self.seed_cursor = raw.get("seed_cursor", 0)
            log.info(
                "Frontier: %d known users, %d never crawled.",
                len(self.users), len(self._pending),
            )
        return self

    def save(self, force: bool = False) -> bool:
        if not self._dirty and not force:
            return False
        if not force and time.monotonic() - self._last_flush < self.flush_every_seconds:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = orjson.dumps({
            "users": self.users,
            "pending": list(self._pending),
            "seed_cursor": self.seed_cursor,
        })
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        self._dirty = False
        self._last_flush = time.monotonic()
        return True

    # -- membership ---------------------------------------------------
    def __contains__(self, user_id: str) -> bool:
        return user_id in self.users

    def __len__(self) -> int:
        return len(self.users)

    def add(self, user_id: str) -> bool:
        """Register a newly discovered user. Returns False if already known."""
        if user_id in self.users:
            return False
        self.users[user_id] = {
            "first_seen_at": isoformat(utcnow()),
            "last_crawled_at": None,
            "next_crawl_at": None,
            "games_found": 0,
            "crawls": 0,
        }
        self._pending.append(user_id)
        self._pending_set.add(user_id)
        self._dirty = True
        return True

    def add_many(self, user_ids: Iterable[str]) -> int:
        return sum(1 for user_id in user_ids if self.add(user_id))

    # -- scheduling ---------------------------------------------------
    @property
    def never_crawled(self) -> int:
        return len(self._pending)

    def due_count(self, now: Optional[datetime] = None) -> int:
        moment = now or utcnow()
        due = 0
        for state in self.users.values():
            next_crawl = parse_iso(state.get("next_crawl_at"))
            if next_crawl is not None and next_crawl <= moment:
                due += 1
        return due + len(self._pending)

    def next_user(self, now: Optional[datetime] = None) -> Optional[str]:
        """Next user to crawl: unseen users first, then the earliest due revisit."""
        while self._pending:
            user_id = self._pending.popleft()
            self._pending_set.discard(user_id)
            if user_id in self.users:
                return user_id

        moment = now or utcnow()
        while self._due_queue:
            user_id = self._due_queue.popleft()
            state = self.users.get(user_id)
            if state is None:
                continue
            next_crawl = parse_iso(state.get("next_crawl_at"))
            if next_crawl is not None and next_crawl <= moment:
                return user_id

        # The due queue is rebuilt in one sorted pass rather than scanning all
        # users on every call; with tens of thousands of users that is the
        # difference between a scan per game and a scan per drain.
        self._rebuild_due_queue(moment)
        if self._due_queue:
            user_id = self._due_queue.popleft()
            return user_id
        return None

    def _rebuild_due_queue(self, moment: datetime) -> None:
        due: List[tuple[datetime, str]] = []
        for user_id, state in self.users.items():
            next_crawl = parse_iso(state.get("next_crawl_at"))
            if next_crawl is not None and next_crawl <= moment:
                due.append((next_crawl, user_id))
        due.sort()
        self._due_queue = deque(user_id for _, user_id in due)

    def mark_crawled(self, user_id: str, games_found: int,
                     recrawl_hours: Optional[float] = None) -> None:
        state = self.users.setdefault(user_id, {
            "first_seen_at": isoformat(utcnow()),
            "games_found": 0,
            "crawls": 0,
        })
        now = utcnow()
        interval = recrawl_hours if recrawl_hours is not None else self.recrawl_hours
        state["last_crawled_at"] = isoformat(now)
        state["next_crawl_at"] = isoformat(now + timedelta(hours=interval))
        state["games_found"] = state.get("games_found", 0) + games_found
        state["crawls"] = state.get("crawls", 0) + 1
        self._pending_set.discard(user_id)
        self._dirty = True

    def stats(self) -> Dict[str, int]:
        return {
            "users_known": len(self.users),
            "users_never_crawled": self.never_crawled,
            "users_due": self.due_count(),
        }
