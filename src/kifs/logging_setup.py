"""Logging configuration shared by the CLI and the long-running service."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(log_dir: Optional[Path] = None, level: int = logging.INFO,
                  filename: str = "kifs.log", to_file: bool = True) -> None:
    """Log to stdout, and additionally to ``log_dir/filename`` when asked.

    Under systemd stdout is captured by journald, so the file handler is mostly
    for interactive runs and for keeping history independent of journald's
    retention.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if to_file and log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / filename, encoding="utf-8"))

    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    formatter = logging.Formatter(_FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # httpx logs every request at INFO, which drowns out the pipeline's own log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
