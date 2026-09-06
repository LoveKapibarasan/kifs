"""The game database and the crawl-record state machine, on SQLite (issue #9).

The public surface is unchanged from the JSON-backed version, so the pipeline,
the query layer and the reports did not have to change. What changed is that
nothing is loaded up front: ``has_game`` is an indexed point lookup and
``due_records`` is an indexed range scan, both independent of dataset size.

Writes are batched into a transaction that commits every
``flush_every_writes`` / ``flush_every_seconds`` and on ``close()``, so the
collector does not pay an fsync per game.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

from kifs.storage.sqlite import Transaction, connect

log = logging.getLogger(__name__)

GAMES_TABLE = "games"
CRAWL_RECORDS_TABLE = "crawl_records"

#: Discovered, KIF not on disk yet — the retry scheduler owns these.
STATUS_KIF_MISSING = "kif_missing"
#: KIF downloaded but not parsed into the game table.
STATUS_KIF_DOWNLOADED = "kif_downloaded"
#: Fully collected.
STATUS_INDEXED = "indexed"
#: Retried up to the attempt limit without success; kept for visibility.
STATUS_KIF_UNAVAILABLE = "kif_unavailable"

#: Statuses the retry scheduler will never pick up again.
TERMINAL_STATUSES = (STATUS_INDEXED, STATUS_KIF_UNAVAILABLE)

#: Columns of ``games`` that are stored as their own column rather than in extra.
GAME_COLUMNS = (
    "game_id", "sente", "gote", "sente_rank", "gote_rank", "sente_rating",
    "gote_rating", "start_time", "end_time", "location", "handicap", "result",
    "total_moves", "moves", "raw_headers", "crawler_user", "crawler_type",
    "crawler_ts",
)
_JSON_COLUMNS = ("moves", "raw_headers")

RECORD_COLUMNS = (
    "game_id", "game_type", "source_user", "discovered_at", "kif_status",
    "has_kif_file", "is_indexed", "indexed_at", "attempts", "next_retry_at",
    "last_attempt_at", "last_error",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(moment: datetime) -> str:
    return moment.isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored timestamp, tolerating the naive values written by v1."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class CrawlRecord(dict):
    """A row of ``crawl_records``; a plain dict with convenience accessors."""

    @property
    def game_id(self) -> str:
        return self["game_id"]

    @property
    def status(self) -> str:
        return self.get("kif_status", STATUS_KIF_MISSING)

    @property
    def attempts(self) -> int:
        return int(self.get("attempts", 0))

    def is_due(self, now: Optional[datetime] = None) -> bool:
        """True when this game should be (re)attempted right now."""
        if self.status in TERMINAL_STATUSES:
            return False
        next_retry = parse_iso(self.get("next_retry_at"))
        if next_retry is None:
            return True
        return next_retry <= (now or utcnow())


def _row_to_game(row: sqlite3.Row) -> dict:
    game = {key: row[key] for key in row.keys() if key != "extra"}
    for column in _JSON_COLUMNS:
        if game.get(column):
            try:
                game[column] = json.loads(game[column])
            except (TypeError, ValueError):
                game[column] = [] if column == "moves" else {}
        else:
            game[column] = [] if column == "moves" else {}
    if row["extra"]:
        try:
            game.update(json.loads(row["extra"]))
        except ValueError:
            pass
    return game


def _row_to_record(row: sqlite3.Row) -> CrawlRecord:
    record = CrawlRecord({key: row[key] for key in row.keys()})
    record["has_kif_file"] = bool(record.get("has_kif_file"))
    record["is_indexed"] = bool(record.get("is_indexed"))
    return record


class KifuDatabase:
    """Facade over the collection database: games plus their crawl state."""

    def __init__(self, path: Path, flush_every_writes: int = 200,
                 flush_every_seconds: float = 60.0, read_only: bool = False):
        self.path = Path(path)
        self.flush_every_writes = flush_every_writes
        self.flush_every_seconds = flush_every_seconds
        self._read_only = read_only
        self._connection: Optional[sqlite3.Connection] = None
        self._transaction: Optional[Transaction] = None

    # -- lifecycle ----------------------------------------------------
    def open(self) -> "KifuDatabase":
        if self._connection is None:
            self._connection = connect(self.path, read_only=self._read_only)
            self._transaction = Transaction(
                self._connection, self.flush_every_writes, self.flush_every_seconds)
            log.info("Opened %s (%d games, %d crawl records).", self.path.name,
                     self.count_games(), self.count_records())
        return self

    @property
    def transaction(self) -> Transaction:
        if self._transaction is None:
            self.open()
        return self._transaction  # type: ignore[return-value]

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.open()
        return self._connection  # type: ignore[return-value]

    def _begin(self) -> None:
        self.transaction.begin()

    def _mark_dirty(self, count: int = 1) -> None:
        self.transaction.mark(count)

    def flush(self, force: bool = False) -> bool:
        """Commit the open transaction. Returns True if anything was committed."""
        return self.transaction.commit(force=force)

    def close(self) -> None:
        if self._connection is None:
            return
        self.flush(force=True)
        self._connection.close()
        self._connection = None
        self._transaction = None

    def __enter__(self) -> "KifuDatabase":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def pending_writes(self) -> int:
        return self.transaction.pending

    # -- games --------------------------------------------------------
    def has_game(self, game_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM games WHERE game_id = ?", (game_id,)).fetchone()
        return row is not None

    def game_ids(self) -> Iterator[str]:
        for row in self.connection.execute("SELECT game_id FROM games"):
            yield row["game_id"]

    def get_game(self, game_id: str) -> Optional[dict]:
        row = self.connection.execute(
            "SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
        return _row_to_game(row) if row else None

    def games(self) -> Iterator[dict]:
        """Stream every game. Never materialises the whole dataset."""
        for row in self.connection.execute("SELECT * FROM games"):
            yield _row_to_game(row)

    def count_games(self) -> int:
        return self.connection.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]

    def upsert_game(self, document: dict) -> None:
        """Insert or merge a game. Fields the caller omits are preserved."""
        game_id = document["game_id"]
        known = {key: document[key] for key in GAME_COLUMNS if key in document}
        extra = {key: value for key, value in document.items()
                 if key not in GAME_COLUMNS}

        existing = self.connection.execute(
            "SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
        if existing is not None:
            merged = {key: existing[key] for key in GAME_COLUMNS}
            merged.update(known)
            previous_extra = {}
            if existing["extra"]:
                try:
                    previous_extra = json.loads(existing["extra"])
                except ValueError:
                    previous_extra = {}
            previous_extra.update(extra)
            extra = previous_extra
        else:
            merged = {key: None for key in GAME_COLUMNS}
            merged.update(known)
            merged["game_id"] = game_id

        for column in _JSON_COLUMNS:
            value = merged.get(column)
            if not isinstance(value, str):
                merged[column] = json.dumps(
                    value if value is not None else ([] if column == "moves" else {}),
                    ensure_ascii=False)

        self._begin()
        columns = list(GAME_COLUMNS) + ["extra"]
        values = [merged.get(key) for key in GAME_COLUMNS]
        values.append(json.dumps(extra, ensure_ascii=False) if extra else None)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{key}=excluded.{key}" for key in columns if key != "game_id")
        self.connection.execute(
            f"INSERT INTO games ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(game_id) DO UPDATE SET {updates}",
            values,
        )
        self._mark_dirty()

    # -- crawl records ------------------------------------------------
    def has_record(self, game_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM crawl_records WHERE game_id = ?", (game_id,)).fetchone()
        return row is not None

    def get_record(self, game_id: str) -> Optional[CrawlRecord]:
        row = self.connection.execute(
            "SELECT * FROM crawl_records WHERE game_id = ?", (game_id,)).fetchone()
        return _row_to_record(row) if row else None

    def records(self) -> Iterator[CrawlRecord]:
        for row in self.connection.execute("SELECT * FROM crawl_records"):
            yield _row_to_record(row)

    def count_records(self) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) AS n FROM crawl_records").fetchone()["n"]

    def add_record(self, game_id: str, game_type: str,
                   source_user: Optional[str]) -> bool:
        """Register a newly discovered game. Returns False if already known.

        Uniqueness is the primary key's job, so a duplicate cannot be created
        even if two writers raced (they cannot — see storage/lock.py — but the
        guarantee no longer depends on that).
        """
        self._begin()
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO crawl_records "
            "(game_id, game_type, source_user, discovered_at, kif_status, "
            " has_kif_file, is_indexed, attempts) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
            (game_id, game_type, source_user, isoformat(utcnow()), STATUS_KIF_MISSING),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            self._mark_dirty()
        return inserted

    def update_record(self, game_id: str, **fields) -> None:
        if not self.has_record(game_id):
            self.add_record(game_id, fields.get("game_type", "sb"),
                            fields.get("source_user"))
        columns = {key: value for key, value in fields.items()
                   if key in RECORD_COLUMNS and key != "game_id"}
        if not columns:
            return
        for key in ("has_kif_file", "is_indexed"):
            if key in columns:
                columns[key] = int(bool(columns[key]))
        assignments = ", ".join(f"{key} = ?" for key in columns)
        self._begin()
        self.connection.execute(
            f"UPDATE crawl_records SET {assignments} WHERE game_id = ?",
            list(columns.values()) + [game_id],
        )
        self._mark_dirty()

    def mark_downloaded(self, game_id: str) -> None:
        self.update_record(
            game_id,
            kif_status=STATUS_KIF_DOWNLOADED,
            has_kif_file=True,
            last_attempt_at=isoformat(utcnow()),
            next_retry_at=None,
            last_error=None,
        )

    def mark_indexed(self, game_id: str) -> None:
        self.update_record(
            game_id,
            kif_status=STATUS_INDEXED,
            has_kif_file=True,
            is_indexed=True,
            indexed_at=isoformat(utcnow()),
            next_retry_at=None,
            last_error=None,
        )

    def schedule_retry(self, game_id: str, error: str, *, base_minutes: float,
                       max_minutes: float, max_attempts: int) -> None:
        """Back off exponentially so an unpublished KIF is retried, not dropped."""
        record = self.get_record(game_id)
        attempts = (record.attempts if record else 0) + 1
        if attempts >= max_attempts:
            self.update_record(
                game_id,
                kif_status=STATUS_KIF_UNAVAILABLE,
                has_kif_file=False,
                attempts=attempts,
                last_attempt_at=isoformat(utcnow()),
                next_retry_at=None,
                last_error=error,
            )
            return
        delay = min(base_minutes * (2 ** (attempts - 1)), max_minutes)
        self.update_record(
            game_id,
            kif_status=STATUS_KIF_MISSING,
            has_kif_file=False,
            attempts=attempts,
            last_attempt_at=isoformat(utcnow()),
            next_retry_at=isoformat(utcnow() + timedelta(minutes=delay)),
            last_error=error,
        )

    def due_records(self, limit: Optional[int] = None,
                    now: Optional[datetime] = None) -> List[CrawlRecord]:
        """Records ready for another download attempt, fewest attempts first.

        Served by ``idx_records_due``; the old version read every record into
        Python and sorted the lot on each call.
        """
        moment = now or utcnow()
        query = (
            "SELECT * FROM crawl_records "
            "WHERE kif_status NOT IN (?, ?) "
            "  AND (next_retry_at IS NULL OR next_retry_at <= ?) "
            "ORDER BY attempts ASC, discovered_at ASC"
        )
        params: list = [STATUS_INDEXED, STATUS_KIF_UNAVAILABLE, isoformat(moment)]
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return [_row_to_record(row) for row in self.connection.execute(query, params)]

    # -- reporting ----------------------------------------------------
    def status_counts(self) -> Dict[str, int]:
        rows = self.connection.execute(
            "SELECT kif_status, COUNT(*) AS n FROM crawl_records GROUP BY kif_status")
        return {row["kif_status"]: row["n"] for row in rows}

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.is_file():
                total += candidate.stat().st_size
        return total
