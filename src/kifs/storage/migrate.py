"""Import the v2 JSON dataset into SQLite (issue #9).

Lossless: every game document, every crawl record and every frontier user is
carried over, including fields that have no column of their own (they land in
``games.extra``). Safe to re-run — everything is an upsert keyed on the id.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import orjson

from kifs.config import Settings
from kifs.storage.database import KifuDatabase, isoformat, utcnow

log = logging.getLogger(__name__)

BATCH = 5000


def migrate_json_to_sqlite(settings: Settings, json_path: Path,
                           frontier_path: Optional[Path] = None,
                           dry_run: bool = False) -> dict:
    json_path = Path(json_path)
    counts = {"games": 0, "crawl_records": 0, "users": 0, "skipped": 0}

    if not json_path.is_file():
        log.warning("%s does not exist; nothing to import.", json_path)
        return counts

    log.info("Reading %s (%.1f MB)...", json_path, json_path.stat().st_size / 1e6)
    raw = orjson.loads(json_path.read_bytes())
    games = raw.get("_default", {})
    records = raw.get("crawl_records", {})
    log.info("Source holds %d games and %d crawl records.", len(games), len(records))

    users = {}
    if frontier_path and Path(frontier_path).is_file():
        frontier_raw = orjson.loads(Path(frontier_path).read_bytes())
        users = frontier_raw.get("users", {})
        log.info("Source frontier holds %d users.", len(users))

    counts["games"] = len(games)
    counts["crawl_records"] = len(records)
    counts["users"] = len(users)
    if dry_run:
        log.info("Dry run; nothing written.")
        return counts

    db = KifuDatabase(settings.db_path, flush_every_writes=BATCH,
                      flush_every_seconds=3600.0).open()
    try:
        for index, document in enumerate(games.values(), start=1):
            if not document.get("game_id"):
                counts["skipped"] += 1
                continue
            db.upsert_game(document)
            if index % BATCH == 0:
                db.flush(force=True)
                log.info("  games: %d/%d", index, len(games))
        db.flush(force=True)

        for index, record in enumerate(records.values(), start=1):
            game_id = record.get("game_id")
            if not game_id:
                counts["skipped"] += 1
                continue
            db.add_record(game_id, record.get("game_type") or "sb",
                          record.get("source_user"))
            fields = {key: value for key, value in record.items() if key != "game_id"}
            if fields:
                db.update_record(game_id, **fields)
            if index % BATCH == 0:
                db.flush(force=True)
                log.info("  crawl_records: %d/%d", index, len(records))
        db.flush(force=True)

        if users:
            db.transaction.begin()
            for index, (user_id, state) in enumerate(users.items(), start=1):
                db.connection.execute(
                    "INSERT INTO users (user_id, first_seen_at, last_crawled_at, "
                    "                   next_crawl_at, games_found, crawls) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "  last_crawled_at = excluded.last_crawled_at, "
                    "  next_crawl_at   = excluded.next_crawl_at, "
                    "  games_found     = excluded.games_found, "
                    "  crawls          = excluded.crawls",
                    (user_id,
                     state.get("first_seen_at") or isoformat(utcnow()),
                     state.get("last_crawled_at"),
                     state.get("next_crawl_at"),
                     int(state.get("games_found") or 0),
                     int(state.get("crawls") or 0)),
                )
                if index % BATCH == 0:
                    db.flush(force=True)
                    db.transaction.begin()
                    log.info("  users: %d/%d", index, len(users))
            db.flush(force=True)

        counts["games_in_db"] = db.count_games()
        counts["records_in_db"] = db.count_records()
        counts["users_in_db"] = db.connection.execute(
            "SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        counts["db_size_mb"] = round(db.size_bytes() / 1e6, 1)
    finally:
        db.close()

    log.info("Imported into %s: %d games, %d records, %d users, %.1f MB.",
             settings.db_path, counts["games_in_db"], counts["records_in_db"],
             counts["users_in_db"], counts["db_size_mb"])
    return counts
