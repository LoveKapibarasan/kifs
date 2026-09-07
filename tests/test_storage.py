"""Storage: SQLite behaviour, and the JSON export that keeps old tooling working."""
import orjson
import pytest
from tinydb import Query, TinyDB

from kifs.storage.database import (
    STATUS_INDEXED,
    STATUS_KIF_MISSING,
    STATUS_KIF_UNAVAILABLE,
    KifuDatabase,
)
from kifs.storage.export import export_tinydb_json


def _db(tmp_path, **kwargs) -> KifuDatabase:
    return KifuDatabase(tmp_path / "kifs.sqlite3", **kwargs).open()


def test_writes_are_batched_until_the_threshold(tmp_path):
    db = _db(tmp_path, flush_every_writes=3, flush_every_seconds=3600)
    db.upsert_game({"game_id": "a"})
    db.upsert_game({"game_id": "b"})
    assert db.pending_writes == 2, "still inside the open transaction"
    db.upsert_game({"game_id": "c"})
    assert db.pending_writes == 0, "third write crosses the threshold and commits"
    db.close()


def test_committed_data_survives_reopening(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "a", "sente": "x"})
    db.close()

    reopened = _db(tmp_path)
    assert reopened.count_games() == 1
    assert reopened.get_game("a")["sente"] == "x"
    reopened.close()


def test_moves_and_headers_round_trip(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "g", "moves": ["２六歩(27)", "３四歩(33)"],
                    "raw_headers": {"場所": "ウォーズ"}, "total_moves": 2})
    db.close()

    game = _db(tmp_path).get_game("g")
    assert game["moves"] == ["２六歩(27)", "３四歩(33)"]
    assert game["raw_headers"]["場所"] == "ウォーズ"


def test_unknown_fields_survive_in_extra(tmp_path):
    """Fields with no column (rank_backfilled_at, ...) must not be dropped."""
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "g", "sente": "x", "rank_backfilled_at": "2026-01-01"})
    db.close()
    assert _db(tmp_path).get_game("g")["rank_backfilled_at"] == "2026-01-01"


def test_upsert_is_idempotent_and_merges(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "g", "sente": "a", "sente_rank": "三段"})
    db.upsert_game({"game_id": "g", "gote": "b"})
    assert db.count_games() == 1
    game = db.get_game("g")
    assert game["gote"] == "b"
    # A field the second write did not mention survives.
    assert game["sente_rank"] == "三段"
    db.close()


def test_add_record_deduplicates(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    assert db.add_record("g", "sb", "u1") is True
    assert db.add_record("g", "sb", "u2") is False
    assert db.count_records() == 1
    db.close()


def test_retry_backs_off_then_gives_up(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.add_record("g", "sb", "u")
    for _ in range(3):
        db.schedule_retry("g", "404", base_minutes=30, max_minutes=1440, max_attempts=3)
    record = db.get_record("g")
    assert record.attempts == 3
    assert record.status == STATUS_KIF_UNAVAILABLE
    assert not record.is_due(), "a given-up record must not be retried forever"
    db.close()


def test_scheduled_retry_is_not_due_yet(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.add_record("g", "sb", "u")
    assert len(db.due_records()) == 1, "a fresh record is due immediately"
    db.schedule_retry("g", "404", base_minutes=30, max_minutes=1440, max_attempts=12)
    assert db.due_records() == [], "backoff must push the retry into the future"
    db.close()


def test_indexed_games_are_never_due(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.add_record("g", "sb", "u")
    db.mark_indexed("g")
    assert db.get_record("g").status == STATUS_INDEXED
    assert db.due_records() == []
    db.close()


def test_due_records_orders_by_attempts(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.add_record("tried", "sb", "u")
    db.add_record("fresh", "sb", "u")
    db.update_record("tried", attempts=3)
    assert [r.game_id for r in db.due_records()] == ["fresh", "tried"]
    db.close()


def test_status_counts(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.add_record("a", "sb", "u")
    db.add_record("b", "sb", "u")
    db.mark_indexed("b")
    assert db.status_counts() == {STATUS_KIF_MISSING: 1, STATUS_INDEXED: 1}
    db.close()


# -- export ------------------------------------------------------------
def test_export_is_readable_by_tinydb(tmp_path):
    """`kifs export` must produce a file the old tooling still opens."""
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "x-y-20260101_000000", "sente": "x", "gote": "y",
                    "total_moves": 3, "moves": ["７六歩(77)"]})
    db.add_record("x-y-20260101_000000", "sb", "x")
    target = tmp_path / "kifu_db.json"
    export_tinydb_json(db, target)
    db.close()

    with TinyDB(target) as tiny:
        game = tiny.get(Query().game_id == "x-y-20260101_000000")
        assert game is not None
        assert game["sente"] == "x"
        assert game["moves"] == ["７六歩(77)"]
        records = tiny.table("crawl_records").all()
        assert len(records) == 1 and records[0]["kif_status"] == STATUS_KIF_MISSING


def test_export_numbers_documents_from_one(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "a"})
    db.upsert_game({"game_id": "b"})
    target = tmp_path / "out.json"
    export_tinydb_json(db, target, include_records=False)
    db.close()

    raw = orjson.loads(target.read_bytes())
    assert sorted(raw["_default"].keys()) == ["1", "2"]
    assert {doc["game_id"] for doc in raw["_default"].values()} == {"a", "b"}


def test_commit_is_safe_when_sqlite_has_no_open_transaction(tmp_path):
    """The flag is not the truth: statements can end a transaction behind our
    back, and committing on a stale flag raised OperationalError."""
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "a"})
    db.connection.execute("COMMIT")          # ended outside the Transaction
    assert db.flush(force=True) is False     # must not raise
    db.close()


def test_close_is_idempotent(tmp_path):
    db = _db(tmp_path, flush_every_writes=1000)
    db.upsert_game({"game_id": "a"})
    db.close()
    db.close()


def test_read_only_open_does_not_write(tmp_path):
    """`kifs status` and `kifs report` run against a live collector; opening
    for write made them fail with "database is locked"."""
    path = tmp_path / "kifs.sqlite3"
    writer = KifuDatabase(path, flush_every_writes=1).open()
    writer.upsert_game({"game_id": "a"})
    writer.flush(force=True)

    # Hold a write transaction open, as the collector does between commits.
    writer.upsert_game({"game_id": "b"})
    writer.connection.execute("SELECT 1")

    reader = KifuDatabase(path, read_only=True).open()
    assert reader.count_games() >= 1
    assert reader.status_counts() == {}
    reader.close()
    writer.close()


def test_read_only_open_creates_a_missing_database(tmp_path):
    db = KifuDatabase(tmp_path / "new.sqlite3", read_only=True).open()
    assert db.count_games() == 0
    db.close()


def test_opening_an_older_database_adds_new_columns(tmp_path):
    """A database created before `uploaded_at` existed must upgrade cleanly.

    The first attempt created the indexes before the columns, so opening a real
    v1 database failed with "no such column: uploaded_at".
    """
    import sqlite3

    path = tmp_path / "old.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
        CREATE TABLE games (game_id TEXT PRIMARY KEY, sente TEXT, gote TEXT,
            sente_rank TEXT, gote_rank TEXT, sente_rating REAL, gote_rating REAL,
            start_time TEXT, end_time TEXT, location TEXT, handicap TEXT,
            result TEXT, total_moves INTEGER, moves TEXT, raw_headers TEXT,
            crawler_user TEXT, crawler_type TEXT, crawler_ts TEXT, extra TEXT);
        CREATE TABLE crawl_records (game_id TEXT PRIMARY KEY, game_type TEXT,
            source_user TEXT, discovered_at TEXT, kif_status TEXT NOT NULL,
            has_kif_file INTEGER NOT NULL DEFAULT 0,
            is_indexed INTEGER NOT NULL DEFAULT 0, indexed_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT,
            last_attempt_at TEXT, last_error TEXT);
        CREATE TABLE users (user_id TEXT PRIMARY KEY, first_seen_at TEXT,
            last_crawled_at TEXT, next_crawl_at TEXT,
            games_found INTEGER NOT NULL DEFAULT 0, crawls INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO crawl_records (game_id, kif_status) VALUES ('a', 'indexed');
    """)
    legacy.commit()
    legacy.close()

    db = KifuDatabase(path).open()
    columns = {row["name"] for row in
               db.connection.execute("PRAGMA table_info(crawl_records)")}
    assert "uploaded_at" in columns
    assert db.get_record("a")["uploaded_at"] is None, "existing rows stay pending"
    db.close()


def test_blocked_schema_upgrade_says_what_to_do(tmp_path):
    """ALTER TABLE needs exclusive access; a running collector denies it. The
    operator should be told that, not "database is locked"."""
    import sqlite3

    from kifs.storage.sqlite import SchemaUpgradeBlocked

    path = tmp_path / "old.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
        CREATE TABLE crawl_records (game_id TEXT PRIMARY KEY, kif_status TEXT NOT NULL);
        CREATE TABLE games (game_id TEXT PRIMARY KEY, extra TEXT);
        CREATE TABLE users (user_id TEXT PRIMARY KEY);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    legacy.commit()
    # Hold a write lock, as the collector does.
    legacy.execute("BEGIN EXCLUSIVE")
    legacy.execute("INSERT INTO meta VALUES ('x', 'y')")

    with pytest.raises(SchemaUpgradeBlocked, match="[Ss]top the collector"):
        KifuDatabase(path, flush_every_writes=1).open()
    legacy.close()
