import pytest

from kifs.clients.shogiwars import players_from_game_id
from kifs.pipeline.indexer import index_kif_file, reconcile
from kifs.storage.database import STATUS_INDEXED, STATUS_KIF_MISSING, KifuDatabase


@pytest.mark.parametrize("game_id,expected", [
    ("mark38-Yukikkoo-20260624_060816", ("mark38", "Yukikkoo")),
    ("aki_eda_p-nkosuke1023-20260614_075031", ("aki_eda_p", "nkosuke1023")),
    ("nodash", (None, None)),
])
def test_players_from_game_id(game_id, expected):
    assert players_from_game_id(game_id) == expected


def test_index_kif_file_writes_game_and_record(settings, sample_kif):
    db = KifuDatabase(settings.db_path, flush_every_writes=1000).open()
    game_id = "mark38-Yukikkoo-20260624_060816"
    path = settings.kif_path(game_id)
    path.write_text(sample_kif, encoding="utf-8")

    assert index_kif_file(db, game_id, path, "mark38", "sb") is True
    game = db.get_game(game_id)
    assert game["sente"] == "mark38" and game["total_moves"] == 4
    assert game["crawler_user"] == "mark38"
    assert db.get_record(game_id).status == STATUS_INDEXED


def test_reconcile_indexes_orphan_files(settings, sample_kif):
    """A .kif on disk that the database lost must come back — this is what makes
    the buffered flush safe (issue #6)."""
    game_id = "mark38-Yukikkoo-20260624_060816"
    settings.kif_path(game_id).write_text(sample_kif, encoding="utf-8")

    db = KifuDatabase(settings.db_path, flush_every_writes=1000).open()
    counts = reconcile(db, settings)

    assert counts["indexed_from_disk"] == 1
    assert counts["records_created"] == 1
    assert db.has_game(game_id)


def test_reconcile_requeues_records_whose_file_vanished(settings):
    db = KifuDatabase(settings.db_path, flush_every_writes=1000).open()
    db.add_record("gone-away-20260101_000000", "sb", "u")
    db.mark_downloaded("gone-away-20260101_000000")

    counts = reconcile(db, settings)

    assert counts["files_missing"] == 1
    record = db.get_record("gone-away-20260101_000000")
    assert record.status == STATUS_KIF_MISSING
    assert record.is_due(), "it must be picked up again, not silently lost"


def test_add_record_deduplicates(settings):
    db = KifuDatabase(settings.db_path, flush_every_writes=1000).open()
    assert db.add_record("g", "sb", "u1") is True
    assert db.add_record("g", "sb", "u2") is False
    assert db.count_records() == 1
