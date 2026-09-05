from kifs.kif.parser import parse_kif, parse_kif_text


def test_parses_headers_moves_and_result(sample_kif):
    parsed = parse_kif_text(sample_kif)
    assert parsed.sente == "mark38"
    assert parsed.gote == "Yukikkoo"
    assert parsed.sente_rank == "三段"
    assert parsed.gote_rank == "二段"
    assert parsed.start_time == "2026/06/24 06:08:16"
    assert parsed.handicap == "平手"
    assert parsed.result == "投了"
    # The terminating "5 投了" line is a result, not a move.
    assert parsed.moves == ["２六歩(27)", "３四歩(33)", "７六歩(77)", "８四歩(83)"]
    assert parsed.total_moves == 4


def test_handicap_games_use_shitate_uwate():
    parsed = parse_kif_text("下手：alice\n上手：bob\n手合割：香落ち\n1 ７六歩(77)\n")
    assert parsed.sente == "alice"
    assert parsed.gote == "bob"


def test_document_shape(sample_kif):
    document = parse_kif_text(sample_kif).to_document("mark38-Yukikkoo-20260624_060816")
    assert document["game_id"] == "mark38-Yukikkoo-20260624_060816"
    assert document["total_moves"] == 4
    assert document["raw_headers"]["場所"].startswith("シ")


def test_missing_file_returns_none(tmp_path):
    assert parse_kif(tmp_path / "nope.kif") is None


def test_shift_jis_fallback(tmp_path):
    path = tmp_path / "legacy.kif"
    path.write_bytes("先手：ふるい\n後手：きろく\n1 ７六歩(77)\n".encode("shift_jis"))
    parsed = parse_kif(path)
    assert parsed is not None
    assert parsed.sente == "ふるい"
