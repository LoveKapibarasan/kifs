"""Parse Shogi Wars KIF text into structured fields.

This module does parsing and nothing else — no HTTP, no database. The previous
``index_to_nosql.py`` mixed all three (issue #1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

#: Lines that terminate a game rather than describe a move.
TERMINATION_TERMS = frozenset({
    "投了", "中断", "持将棋", "千日手", "合意", "切れ負け", "反則手",
    "時間切れ", "反則勝ち", "詰み", "不戦勝", "不戦敗",
})


@dataclass
class ParsedKif:
    sente: Optional[str] = None
    gote: Optional[str] = None
    sente_rank: Optional[str] = None
    gote_rank: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    handicap: Optional[str] = None
    result: Optional[str] = None
    moves: List[str] = field(default_factory=list)
    raw_headers: Dict[str, str] = field(default_factory=dict)

    @property
    def total_moves(self) -> int:
        return len(self.moves)

    def to_document(self, game_id: str) -> dict:
        """Render the document shape stored in ``kifu_db.json``."""
        return {
            "game_id": game_id,
            "sente": self.sente,
            "gote": self.gote,
            "sente_rank": self.sente_rank,
            "gote_rank": self.gote_rank,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "location": self.location,
            "handicap": self.handicap,
            "result": self.result,
            "moves": self.moves,
            "total_moves": self.total_moves,
            "raw_headers": self.raw_headers,
        }


def parse_kif_text(text: str) -> ParsedKif:
    """Parse KIF content already in memory."""
    headers: Dict[str, str] = {}
    moves: List[str] = []
    result: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Header lines look like "先手：name" and never start with a digit.
        if "：" in line and not line[0].isdigit():
            key, _, value = line.partition("：")
            headers[key.strip()] = value.strip()
            continue

        # Move lines look like "1 ２六歩(27)"; the last one may be "85 投了".
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            content = parts[1].strip()
            if content in TERMINATION_TERMS:
                result = content
            else:
                moves.append(content)
        elif line in TERMINATION_TERMS:
            result = line

    return ParsedKif(
        # 下手/上手 are the handicap-game equivalents of 先手/後手.
        sente=headers.get("先手") or headers.get("下手"),
        gote=headers.get("後手") or headers.get("上手"),
        # 段級 (rank at game time) is only present in KIFs from the analytics API.
        sente_rank=headers.get("先手段級") or headers.get("下手段級"),
        gote_rank=headers.get("後手段級") or headers.get("上手段級"),
        start_time=headers.get("開始日時"),
        end_time=headers.get("終了日時"),
        location=headers.get("場所"),
        handicap=headers.get("手合割"),
        result=result,
        moves=moves,
        raw_headers=headers,
    )


def parse_kif(path: str | Path) -> Optional[ParsedKif]:
    """Parse a KIF file, tolerating the legacy Shift_JIS encoding."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="shift_jis")
        except (UnicodeDecodeError, OSError):
            return None
    except OSError:
        return None
    return parse_kif_text(text)
