"""SQLite connection, schema and migration (issue #9).

The single-JSON store worked while the dataset was small, but collection
outgrew it in a day: 38k games / 157MB / 1.0s to load became 75k / 306MB /
34.5s, and every flush rewrote the whole file. The cost was structural — one
document per game in one JSON object — not something a faster serialiser fixes.

SQLite gives O(1) point lookups without loading anything, constant memory, and
``game_id`` uniqueness enforced by the engine rather than by a Python dict.
``kifs export`` still writes the old TinyDB-shaped JSON for anything that reads
it.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    sente        TEXT,
    gote         TEXT,
    sente_rank   TEXT,
    gote_rank    TEXT,
    sente_rating REAL,
    gote_rating  REAL,
    start_time   TEXT,
    end_time     TEXT,
    location     TEXT,
    handicap     TEXT,
    result       TEXT,
    total_moves  INTEGER,
    moves        TEXT,   -- JSON array
    raw_headers  TEXT,   -- JSON object
    crawler_user TEXT,
    crawler_type TEXT,
    crawler_ts   TEXT,
    extra        TEXT    -- JSON object for fields added later
);
CREATE INDEX IF NOT EXISTS idx_games_sente  ON games(sente);
CREATE INDEX IF NOT EXISTS idx_games_gote   ON games(gote);
CREATE INDEX IF NOT EXISTS idx_games_result ON games(result);
CREATE INDEX IF NOT EXISTS idx_games_start  ON games(start_time);

CREATE TABLE IF NOT EXISTS crawl_records (
    game_id         TEXT PRIMARY KEY,
    game_type       TEXT,
    source_user     TEXT,
    discovered_at   TEXT,
    kif_status      TEXT NOT NULL,
    has_kif_file    INTEGER NOT NULL DEFAULT 0,
    is_indexed      INTEGER NOT NULL DEFAULT 0,
    indexed_at      TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,
    last_attempt_at TEXT,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_status ON crawl_records(kif_status);
-- Serves due_records(): the retry scheduler's only hot query.
CREATE INDEX IF NOT EXISTS idx_records_due
    ON crawl_records(kif_status, next_retry_at, attempts);

CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    first_seen_at   TEXT,
    last_crawled_at TEXT,
    next_crawl_at   TEXT,
    games_found     INTEGER NOT NULL DEFAULT 0,
    crawls          INTEGER NOT NULL DEFAULT 0
);
-- Serves next_user(): unseen users have next_crawl_at IS NULL and sort first.
CREATE INDEX IF NOT EXISTS idx_users_next ON users(next_crawl_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Open the database, creating the schema when it is new."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row

    # WAL lets `kifs status` read while the collector writes.
    connection.execute("PRAGMA journal_mode=WAL")
    # NORMAL is durable across process crashes (only a host crash can lose the
    # last transactions), and the reconcile pass repairs that from the .kif files.
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    if not read_only:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
    return connection


def get_meta(connection: sqlite3.Connection, key: str, default: str | None = None):
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


class Transaction:
    """A batched write transaction shared by every writer on one connection.

    SQLite allows a single writer, so the database and the frontier must not
    each open their own transaction on the same file. They share this one; it
    commits every ``flush_every_writes`` / ``flush_every_seconds`` and on
    ``commit(force=True)`` at shutdown.
    """

    def __init__(self, connection: sqlite3.Connection,
                 flush_every_writes: int = 200, flush_every_seconds: float = 60.0):
        self.connection = connection
        self.flush_every_writes = flush_every_writes
        self.flush_every_seconds = flush_every_seconds
        self._open = False
        self._pending = 0
        self._last_commit = time.monotonic()

    @property
    def pending(self) -> int:
        return self._pending

    def begin(self) -> None:
        # `connection.in_transaction` is the truth, not our flag: some
        # statements (DDL via executescript, an error rollback) end a
        # transaction behind our back, and committing a flag that no longer
        # matches raises "cannot commit - no transaction is active".
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN")
        self._open = True

    def mark(self, count: int = 1) -> None:
        """Record writes and commit once a threshold is crossed."""
        self._pending += count
        if self._pending >= self.flush_every_writes:
            self.commit()
        elif time.monotonic() - self._last_commit >= self.flush_every_seconds:
            self.commit()

    def commit(self, force: bool = False) -> bool:
        if not self.connection.in_transaction:
            self._open = False
            self._pending = 0
            return False
        if self._pending == 0 and not force:
            return False
        self.connection.execute("COMMIT")
        self._open = False
        self._pending = 0
        self._last_commit = time.monotonic()
        return True
