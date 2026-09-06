import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kifs.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        kif_dir=data_dir / "kif",
        db_path=data_dir / "kifs.sqlite3",
        state_dir=data_dir / "state",
        legacy_db_path=data_dir / "kifu_db.json",
        legacy_frontier_path=data_dir / "state" / "frontier.json",
        log_dir=data_dir / "logs",
        lock_path=data_dir / "state" / "kifs.lock",
    )
    settings.request_delay = 0.0
    settings.flush_every_writes = 5
    settings.flush_every_seconds = 3600.0
    settings.ensure_dirs()
    return settings


SAMPLE_KIF = """開始日時：2026/06/24 06:08:16
終了日時：2026/06/24 06:12:03
場所：シ ョ ウ ギ ウ ォ ー ズ
手合割：平手
先手：mark38
後手：Yukikkoo
先手段級：三段
後手段級：二段
手数----指手---------消費時間--
1 ２六歩(27)
2 ３四歩(33)
3 ７六歩(77)
4 ８四歩(83)
5 投了
"""


@pytest.fixture
def sample_kif() -> str:
    return SAMPLE_KIF
