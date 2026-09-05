"""The game database and the crawl-record state machine.

Every ``game_id`` lookup goes through an in-memory ``game_id -> doc_id`` index,
so membership tests and updates are O(1). The old pipeline used TinyDB's
``contains``/``update``, each a full scan of 38k records, several times per
collected game (issue #4).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

from kifs.storage.jsonstore import BufferedJSONStore

log = logging.getLogger(__name__)

GAMES_TABLE = "_default"
CRAWL_RECORDS_TABLE = "crawl_records"

#: Discovered, KIF not on disk yet — the retry scheduler owns these.
STATUS_KIF_MISSING = "kif_missing"
#: KIF downloaded but not parsed into the game table.
STATUS_KIF_DOWNLOADED = "kif_downloaded"
#: Fully collected.
STATUS_INDEXED = "indexed"
#: Retried up to the attempt limit without success; kept for visibility.
STATUS_KIF_UNAVAILABLE = "kif_unavailable"

ACTIVE_STATUSES = (STATUS_KIF_MISSING, STATUS_KIF_DOWNLOADED)


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
        if self.status in (STATUS_INDEXED, STATUS_KIF_UNAVAILABLE):
            return False
        next_retry = parse_iso(self.get("next_retry_at"))
        if next_retry is None:
            return True
        return next_retry <= (now or utcnow())


class KifuDatabase:
    """Facade over ``kifu_db.json``: games plus their crawl state."""

    def __init__(self, path: Path, flush_every_writes: int = 200,
                 flush_every_seconds: float = 60.0):
        self._store = BufferedJSONStore(path, flush_every_writes, flush_every_seconds)
        self._game_index: Dict[str, str] = {}
        self._record_index: Dict[str, str] = {}
        self._loaded = False

    # -- lifecycle ----------------------------------------------------
    def open(self) -> "KifuDatabase":
        if self._loaded:
            return self
        self._store.load()
        for doc_id, doc in self._store.table(GAMES_TABLE).items():
            game_id = doc.get("game_id")
            if game_id:
                self._game_index[game_id] = doc_id
        for doc_id, doc in self._store.table(CRAWL_RECORDS_TABLE).items():
            game_id = doc.get("game_id")
            if game_id:
                self._record_index[game_id] = doc_id
        log.info(
            "Indexed %d games and %d crawl records for O(1) lookup.",
            len(self._game_index), len(self._record_index),
        )
        self._loaded = True
        return self

    def flush(self, force: bool = False) -> bool:
        return self._store.flush(force=force)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "KifuDatabase":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def pending_writes(self) -> int:
        return self._store.pending_writes

    # -- games --------------------------------------------------------
    def has_game(self, game_id: str) -> bool:
        return game_id in self._game_index

    def game_ids(self) -> Iterable[str]:
        return self._game_index.keys()

    def get_game(self, game_id: str) -> Optional[dict]:
        doc_id = self._game_index.get(game_id)
        if doc_id is None:
            return None
        return self._store.table(GAMES_TABLE).get(doc_id)

    def games(self) -> Iterator[dict]:
        return iter(self._store.table(GAMES_TABLE).values())

    def count_games(self) -> int:
        return len(self._game_index)

    def upsert_game(self, document: dict) -> None:
        game_id = document["game_id"]
        doc_id = self._game_index.get(game_id)
        if doc_id is None:
            self._game_index[game_id] = self._store.insert(GAMES_TABLE, document)
        else:
            # Preserve fields written by other passes (e.g. rank enrichment).
            existing = self._store.table(GAMES_TABLE)[doc_id]
            existing.update(document)
            self._store.mark_dirty()

    # -- crawl records ------------------------------------------------
    def has_record(self, game_id: str) -> bool:
        return game_id in self._record_index

    def get_record(self, game_id: str) -> Optional[CrawlRecord]:
        doc_id = self._record_index.get(game_id)
        if doc_id is None:
            return None
        return CrawlRecord(self._store.table(CRAWL_RECORDS_TABLE)[doc_id])

    def records(self) -> Iterator[CrawlRecord]:
        for doc in self._store.table(CRAWL_RECORDS_TABLE).values():
            yield CrawlRecord(doc)

    def count_records(self) -> int:
        return len(self._record_index)

    def add_record(self, game_id: str, game_type: str, source_user: Optional[str]) -> bool:
        """Register a newly discovered game. Returns False if already known.

        This is the only place a crawl record is created, so ``game_id``
        uniqueness is enforced in exactly one code path (issue #4).
        """
        if game_id in self._record_index:
            return False
        document = {
            "game_id": game_id,
            "game_type": game_type,
            "source_user": source_user,
            "discovered_at": isoformat(utcnow()),
            "kif_status": STATUS_KIF_MISSING,
            "has_kif_file": False,
            "is_indexed": False,
            "attempts": 0,
            "next_retry_at": None,
            "last_attempt_at": None,
            "last_error": None,
        }
        self._record_index[game_id] = self._store.insert(CRAWL_RECORDS_TABLE, document)
        return True

    def update_record(self, game_id: str, **fields) -> None:
        doc_id = self._record_index.get(game_id)
        if doc_id is None:
            self.add_record(game_id, fields.get("game_type", "sb"), fields.get("source_user"))
            doc_id = self._record_index[game_id]
        self._store.table(CRAWL_RECORDS_TABLE)[doc_id].update(fields)
        self._store.mark_dirty()

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
        """Records ready for another download attempt, oldest schedule first."""
        moment = now or utcnow()
        due = [record for record in self.records() if record.is_due(moment)]
        due.sort(key=lambda r: (r.attempts, r.get("discovered_at") or ""))
        return due[:limit] if limit else due

    # -- reporting ----------------------------------------------------
    def status_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records():
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts
