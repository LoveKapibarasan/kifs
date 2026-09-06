"""Importing the v2 JSON dataset into SQLite must lose nothing."""
import orjson

from kifs.storage.database import STATUS_INDEXED, KifuDatabase
from kifs.storage.migrate import migrate_json_to_sqlite


def _write_v2_dataset(settings):
    settings.legacy_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.legacy_db_path.write_bytes(orjson.dumps({
        "_default": {
            "1": {"game_id": "a-b-20260101_000000", "sente": "a", "gote": "b",
                  "sente_rank": "三段", "total_moves": 2,
                  "moves": ["７六歩(77)", "３四歩(33)"],
                  "raw_headers": {"手合割": "平手"},
                  "rank_backfilled_at": "2026-07-01T00:00:00"},
            "2": {"game_id": "c-d-20260102_000000", "sente": "c", "gote": "d",
                  "total_moves": 1, "moves": ["２六歩(27)"], "raw_headers": {}},
        },
        "crawl_records": {
            "1": {"game_id": "a-b-20260101_000000", "game_type": "sb",
                  "source_user": "a", "kif_status": "indexed",
                  "has_kif_file": True, "is_indexed": True, "attempts": 0},
            "2": {"game_id": "c-d-20260102_000000", "game_type": "sb",
                  "source_user": "c", "kif_status": "kif_missing",
                  "has_kif_file": False, "is_indexed": False, "attempts": 2,
                  "last_error": "404"},
        },
    }))
    settings.legacy_frontier_path.parent.mkdir(parents=True, exist_ok=True)
    settings.legacy_frontier_path.write_bytes(orjson.dumps({
        "users": {
            "a": {"first_seen_at": "2026-01-01T00:00:00", "crawls": 2,
                  "last_crawled_at": "2026-01-02T00:00:00",
                  "next_crawl_at": "2026-01-03T00:00:00", "games_found": 7},
            "b": {"first_seen_at": "2026-01-01T00:00:00", "crawls": 0,
                  "last_crawled_at": None, "next_crawl_at": None, "games_found": 0},
        },
        "pending": ["b"],
    }))


def test_migration_carries_everything_across(settings):
    _write_v2_dataset(settings)
    counts = migrate_json_to_sqlite(settings, settings.legacy_db_path,
                                    settings.legacy_frontier_path)
    assert counts["games_in_db"] == 2
    assert counts["records_in_db"] == 2
    assert counts["users_in_db"] == 2

    db = KifuDatabase(settings.db_path).open()
    game = db.get_game("a-b-20260101_000000")
    assert game["sente_rank"] == "三段"
    assert game["moves"] == ["７六歩(77)", "３四歩(33)"]
    assert game["raw_headers"]["手合割"] == "平手"
    # A field with no column of its own must survive in `extra`.
    assert game["rank_backfilled_at"] == "2026-07-01T00:00:00"

    assert db.get_record("a-b-20260101_000000").status == STATUS_INDEXED
    retried = db.get_record("c-d-20260102_000000")
    assert retried.attempts == 2 and retried["last_error"] == "404"
    db.close()


def test_migration_preserves_the_revisit_schedule(settings):
    _write_v2_dataset(settings)
    migrate_json_to_sqlite(settings, settings.legacy_db_path,
                           settings.legacy_frontier_path)
    db = KifuDatabase(settings.db_path).open()
    from kifs.storage.frontier import Frontier

    frontier = Frontier(db)
    assert len(frontier) == 2
    assert frontier.never_crawled == 1, "user b was never crawled"
    assert frontier.get("a")["games_found"] == 7
    db.close()


def test_migration_is_rerunnable(settings):
    _write_v2_dataset(settings)
    migrate_json_to_sqlite(settings, settings.legacy_db_path,
                           settings.legacy_frontier_path)
    counts = migrate_json_to_sqlite(settings, settings.legacy_db_path,
                                    settings.legacy_frontier_path)
    assert counts["games_in_db"] == 2, "re-running must not duplicate anything"
    assert counts["records_in_db"] == 2
    assert counts["users_in_db"] == 2


def test_dry_run_writes_nothing(settings):
    _write_v2_dataset(settings)
    counts = migrate_json_to_sqlite(settings, settings.legacy_db_path,
                                    settings.legacy_frontier_path, dry_run=True)
    assert counts["games"] == 2
    assert not settings.db_path.exists()
