"""The single path from a ``.kif`` file to a database document (issue #1).

v1 had this same parse-and-upsert sequence written out three times, in
``run_pipeline.py``, ``kif_downloader.py`` and ``index_to_nosql.py``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from kifs.config import Settings
from kifs.kif.parser import parse_kif
from kifs.storage.database import (
    STATUS_INDEXED,
    STATUS_KIF_DOWNLOADED,
    STATUS_KIF_MISSING,
    KifuDatabase,
    isoformat,
    utcnow,
)

log = logging.getLogger(__name__)


def index_kif_file(db: KifuDatabase, game_id: str, path: Path,
                   source_user: Optional[str] = None,
                   game_type: Optional[str] = None) -> bool:
    """Parse one KIF file and upsert it. Returns True when the game got indexed."""
    parsed = parse_kif(path)
    if parsed is None:
        db.update_record(
            game_id,
            kif_status=STATUS_KIF_DOWNLOADED if path.is_file() else STATUS_KIF_MISSING,
            has_kif_file=path.is_file(),
            is_indexed=False,
            last_attempt_at=isoformat(utcnow()),
            last_error="KIF file unreadable",
        )
        return False

    document = parsed.to_document(game_id)
    record = db.get_record(game_id)
    document.update({
        "crawler_user": source_user or (record or {}).get("source_user"),
        "crawler_type": game_type or (record or {}).get("game_type"),
        "crawler_ts": (record or {}).get("discovered_at") or isoformat(utcnow()),
    })
    db.upsert_game(document)
    db.mark_indexed(game_id)
    return True


def reconcile(db: KifuDatabase, settings: Settings) -> dict:
    """Bring the database back in line with what is actually on disk (issue #3).

    Runs at every service start. It covers three drifts:

    * a ``.kif`` exists on disk but the game is not in the database — index it
      (this is also what makes the buffered flush safe: anything lost with an
      unflushed buffer is recovered here);
    * a record claims ``has_kif_file`` but the file is gone — reset it so the
      downloader fetches it again;
    * a ``.kif`` exists for a game that has no crawl record at all — create one.
    """
    counts = {"indexed_from_disk": 0, "records_created": 0, "files_missing": 0, "scanned": 0}

    on_disk: dict[str, Path] = {}
    for path in settings.kif_dir.glob("*.kif"):
        on_disk[path.stem] = path
    counts["scanned"] = len(on_disk)

    # Read the two id sets once instead of doing a point query per file; at
    # 75k games that is two scans rather than 150k lookups.
    known_games = set(db.game_ids())
    known_records = {
        row["game_id"]: row["kif_status"]
        for row in db.connection.execute("SELECT game_id, kif_status FROM crawl_records")
    }

    for game_id, path in on_disk.items():
        if game_id not in known_records:
            db.add_record(game_id, settings.game_types[0], None)
            counts["records_created"] += 1
        if game_id not in known_games:
            if index_kif_file(db, game_id, path):
                counts["indexed_from_disk"] += 1
        elif known_records.get(game_id) != STATUS_INDEXED:
            db.mark_indexed(game_id)

    # Records claiming a file that is no longer there. Collect the ids first:
    # updating a table while a cursor is open on it is not safe.
    orphaned = [
        row["game_id"] for row in db.connection.execute(
            "SELECT game_id FROM crawl_records "
            "WHERE has_kif_file = 1 OR kif_status IN (?, ?)",
            (STATUS_INDEXED, STATUS_KIF_DOWNLOADED))
        if row["game_id"] not in on_disk
    ]
    for game_id in orphaned:
        # The file vanished; make the downloader pick it up again.
        db.update_record(
            game_id,
            kif_status=STATUS_KIF_MISSING,
            has_kif_file=False,
            is_indexed=False,
            next_retry_at=None,
            last_error="KIF file missing on disk",
        )
        counts["files_missing"] += 1

    log.info(
        "Reconcile: scanned %d files, indexed %d from disk, created %d records, "
        "reset %d records with missing files.",
        counts["scanned"], counts["indexed_from_disk"],
        counts["records_created"], counts["files_missing"],
    )
    return counts
