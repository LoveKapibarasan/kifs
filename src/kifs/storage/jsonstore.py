"""Buffered, atomically-written JSON document store (issue #6).

``kifu_db.json`` is 198MB. TinyDB re-serialises the whole file on every single
``insert``/``update``, which the old pipeline did up to three times per game —
roughly 600MB of serialisation per collected game, getting worse as the dataset
grows.

This store keeps the same on-disk layout (``{table: {doc_id: document}}``, which
TinyDB reads back unchanged — see ``tests/test_storage.py``) but holds it in
memory and flushes on a write/time threshold, on ``close()``, and on the
service's shutdown signal. A crash can therefore lose at most one flush window
of *index* entries; the ``.kif`` files themselves are written to disk as they
arrive, so the startup reconcile pass (issue #3) restores anything lost.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

import orjson

log = logging.getLogger(__name__)

Table = Dict[str, dict]


class BufferedJSONStore:
    def __init__(self, path: Path, flush_every_writes: int = 200,
                 flush_every_seconds: float = 60.0):
        self.path = Path(path)
        self.flush_every_writes = flush_every_writes
        self.flush_every_seconds = flush_every_seconds
        self._tables: Dict[str, Table] = {}
        self._next_ids: Dict[str, int] = {}
        self._pending_writes = 0
        self._last_flush = time.monotonic()
        self._loaded = False

    # -- loading ------------------------------------------------------
    def load(self) -> None:
        if self._loaded:
            return
        if self.path.is_file() and self.path.stat().st_size > 0:
            started = time.monotonic()
            raw = orjson.loads(self.path.read_bytes())
            if not isinstance(raw, dict):
                raise ValueError(f"{self.path} is not a TinyDB-shaped object")
            self._tables = {name: dict(rows) for name, rows in raw.items()}
            log.info(
                "Loaded %s (%.1f MB, %s) in %.1fs.",
                self.path.name,
                self.path.stat().st_size / 1e6,
                ", ".join(f"{n}={len(r)}" for n, r in self._tables.items()) or "empty",
                time.monotonic() - started,
            )
        else:
            self._tables = {}
        for name, rows in self._tables.items():
            self._next_ids[name] = max((int(k) for k in rows), default=0) + 1
        self._loaded = True

    # -- table access -------------------------------------------------
    def table(self, name: str) -> Table:
        self.load()
        if name not in self._tables:
            self._tables[name] = {}
            self._next_ids[name] = 1
        return self._tables[name]

    def insert(self, name: str, document: dict) -> str:
        table = self.table(name)
        doc_id = str(self._next_ids.get(name, 1))
        self._next_ids[name] = int(doc_id) + 1
        table[doc_id] = document
        self.mark_dirty()
        return doc_id

    def replace(self, name: str, doc_id: str, document: dict) -> None:
        self.table(name)[doc_id] = document
        self.mark_dirty()

    def mark_dirty(self, count: int = 1) -> None:
        self._pending_writes += count
        if self._pending_writes >= self.flush_every_writes:
            self.flush()
        elif time.monotonic() - self._last_flush >= self.flush_every_seconds:
            self.flush()

    # -- persistence --------------------------------------------------
    @property
    def pending_writes(self) -> int:
        return self._pending_writes

    def flush(self, force: bool = False) -> bool:
        """Write the buffer out atomically. Returns True if a write happened."""
        if not self._loaded or (self._pending_writes == 0 and not force):
            return False
        started = time.monotonic()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # No OPT_INDENT_2: indentation inflates the file and slows every read
        # and write. `kifs search` / `kifs stats` are how humans read this data.
        payload = orjson.dumps(self._tables)
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        log.info(
            "Flushed %d pending writes to %s (%.1f MB, %.1fs).",
            self._pending_writes, self.path.name, len(payload) / 1e6,
            time.monotonic() - started,
        )
        self._pending_writes = 0
        self._last_flush = time.monotonic()
        return True

    def close(self) -> None:
        self.flush(force=self._pending_writes > 0)
