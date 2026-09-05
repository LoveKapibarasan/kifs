"""An advisory lock file so two collectors never share one database (issue #4)."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class LockHeld(RuntimeError):
    """Another process already holds the collector lock."""


class ProcessLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "w")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise LockHeld(
                f"another kifs process holds {self.path}; refusing to start a second writer"
            ) from exc
        self._handle.write(f"{os.getpid()}\n")
        self._handle.flush()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
