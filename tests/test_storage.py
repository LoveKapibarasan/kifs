"""The buffered store must stay byte-compatible with what TinyDB reads."""
import orjson
from tinydb import Query, TinyDB

from kifs.storage.database import (
    STATUS_INDEXED,
    STATUS_KIF_MISSING,
    STATUS_KIF_UNAVAILABLE,
    KifuDatabase,
)
from kifs.storage.jsonstore import BufferedJSONStore


def test_writes_are_buffered_until_the_threshold(tmp_path):
    path = tmp_path / "db.json"
    store = BufferedJSONStore(path, flush_every_writes=3, flush_every_seconds=3600)
    store.insert("_default", {"game_id": "a"})
    store.insert("_default", {"game_id": "b"})
    assert not path.exists(), "should not have touched disk yet"
    store.insert("_default", {"game_id": "c"})
    assert path.exists(), "third write crosses the threshold"
    assert store.pending_writes == 0


def test_file_is_readable_by_tinydb(tmp_path):
    """kifu_db.json stays a valid TinyDB file, so existing tooling still works."""
    path = tmp_path / "kifu_db.json"
    db = KifuDatabase(path, flush_every_writes=1000).open()
    db.upsert_game({"game_id": "x-y-20260101_000000", "sente": "x", "gote": "y",
                    "total_moves": 3})
    db.add_record("x-y-20260101_000000", "sb", "x")
    db.close()

    with TinyDB(path) as tiny:
        game = tiny.get(Query().game_id == "x-y-20260101_000000")
        assert game is not None and game["sente"] == "x"
        records = tiny.table("crawl_records").all()
        assert len(records) == 1 and records[0]["kif_status"] == STATUS_KIF_MISSING


def test_reopening_preserves_and_extends_ids(tmp_path):
    path = tmp_path / "kifu_db.json"
    db = KifuDatabase(path, flush_every_writes=1).open()
    db.upsert_game({"game_id": "a"})
    db.close()

    reopened = KifuDatabase(path, flush_every_writes=1).open()
    reopened.upsert_game({"game_id": "b"})
    reopened.close()

    raw = orjson.loads(path.read_bytes())
    assert sorted(raw["_default"].keys()) == ["1", "2"]
    assert {doc["game_id"] for doc in raw["_default"].values()} == {"a", "b"}


def test_upsert_is_idempotent_and_merges(tmp_path):
    db = KifuDatabase(tmp_path / "db.json", flush_every_writes=1000).open()
    db.upsert_game({"game_id": "g", "sente": "a", "sente_rank": "三段"})
    db.upsert_game({"game_id": "g", "sente": "a", "gote": "b"})
    assert db.count_games() == 1
    game = db.get_game("g")
    assert game["gote"] == "b"
    # A field the second write did not mention survives.
    assert game["sente_rank"] == "三段"


def test_retry_backs_off_then_gives_up(tmp_path):
    db = KifuDatabase(tmp_path / "db.json", flush_every_writes=1000).open()
    db.add_record("g", "sb", "u")
    for _ in range(3):
        db.schedule_retry("g", "404", base_minutes=30, max_minutes=1440, max_attempts=3)
    record = db.get_record("g")
    assert record.attempts == 3
    assert record.status == STATUS_KIF_UNAVAILABLE
    assert not record.is_due(), "a given-up record must not be retried forever"


def test_scheduled_retry_is_not_due_yet(tmp_path):
    db = KifuDatabase(tmp_path / "db.json", flush_every_writes=1000).open()
    db.add_record("g", "sb", "u")
    assert len(db.due_records()) == 1, "a fresh record is due immediately"
    db.schedule_retry("g", "404", base_minutes=30, max_minutes=1440, max_attempts=12)
    assert db.due_records() == [], "backoff must push the retry into the future"


def test_indexed_games_are_never_due(tmp_path):
    db = KifuDatabase(tmp_path / "db.json", flush_every_writes=1000).open()
    db.add_record("g", "sb", "u")
    db.mark_indexed("g")
    assert db.get_record("g").status == STATUS_INDEXED
    assert db.due_records() == []
