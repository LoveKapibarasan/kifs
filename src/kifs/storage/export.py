"""Export the SQLite database back to the TinyDB-shaped JSON (issue #9).

Anything that still reads ``kifu_db.json`` — ad-hoc scripts, the KIF-to-CSA
conversion for training data — keeps working: run ``kifs export`` and point it
at the same file it always read.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import orjson

from kifs.storage.database import GAME_COLUMNS, KifuDatabase

log = logging.getLogger(__name__)


def export_tinydb_json(db: KifuDatabase, path: Path,
                       include_records: bool = True,
                       indent: bool = False) -> dict:
    """Write ``{"_default": {...}, "crawl_records": {...}}`` to ``path``.

    Documents are numbered from 1 in table order, which is what TinyDB does with
    a file it wrote itself.
    """
    games = {}
    for index, game in enumerate(db.games(), start=1):
        games[str(index)] = game

    tables = {"_default": games}
    if include_records:
        records = {}
        for index, record in enumerate(db.records(), start=1):
            records[str(index)] = dict(record)
        tables["crawl_records"] = records

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    option = orjson.OPT_INDENT_2 if indent else 0
    with open(tmp, "wb") as handle:
        handle.write(orjson.dumps(tables, option=option))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

    counts = {name: len(rows) for name, rows in tables.items()}
    log.info("Exported %s to %s (%.1f MB).",
             ", ".join(f"{n}={c}" for n, c in counts.items()),
             path, path.stat().st_size / 1e6)
    return counts
