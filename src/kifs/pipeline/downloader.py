"""Download KIF text and put it on disk, then hand it to the indexer."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from kifs.clients.kishin import KifNotAvailable, KishinAnalyticsClient
from kifs.config import Settings
from kifs.pipeline.indexer import index_kif_file
from kifs.storage.database import KifuDatabase

log = logging.getLogger(__name__)


class Downloader:
    def __init__(self, kishin: KishinAnalyticsClient, db: KifuDatabase, settings: Settings):
        self._kishin = kishin
        self._db = db
        self._settings = settings

    async def collect(self, game_id: str, source_user: Optional[str] = None,
                      game_type: Optional[str] = None) -> str:
        """Fetch (if needed) and index one game.

        Returns one of ``"indexed"``, ``"already"``, ``"retry"`` or ``"error"``.
        The ``.kif`` is written to disk *before* the database is touched, so a
        crash between the two is repaired by the startup reconcile pass.
        """
        path = self._settings.kif_path(game_id)

        if path.is_file():
            if self._db.has_game(game_id):
                return "already"
            return "indexed" if index_kif_file(
                self._db, game_id, path, source_user, game_type) else "error"

        try:
            kif_text = await self._kishin.fetch_kif(game_id)
        except KifNotAvailable as exc:
            self._db.schedule_retry(
                game_id, str(exc),
                base_minutes=self._settings.kif_retry_base_minutes,
                max_minutes=self._settings.kif_retry_max_minutes,
                max_attempts=self._settings.kif_max_attempts,
            )
            return "retry"
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            self._db.schedule_retry(
                game_id, f"HTTP {status}",
                base_minutes=self._settings.kif_retry_base_minutes,
                max_minutes=self._settings.kif_retry_max_minutes,
                max_attempts=self._settings.kif_max_attempts,
            )
            if status in (401, 403):
                log.error("KIF API rejected the session cookie (HTTP %s) — "
                          "ANALYTICS_SESSION has probably expired.", status)
            return "retry"
        except Exception as exc:
            self._db.schedule_retry(
                game_id, f"{type(exc).__name__}: {exc}",
                base_minutes=self._settings.kif_retry_base_minutes,
                max_minutes=self._settings.kif_retry_max_minutes,
                max_attempts=self._settings.kif_max_attempts,
            )
            return "retry"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(kif_text, encoding="utf-8")
        self._db.mark_downloaded(game_id)
        return "indexed" if index_kif_file(
            self._db, game_id, path, source_user, game_type) else "error"
