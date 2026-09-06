"""Re-download KIFs for games indexed before the analytics API existed.

Those KIFs carry no 段級 header. The current API embeds 先手段級/後手段級 (rank at
game time), so re-fetching gives the accurate value. Resumable: games that
already have a rank are skipped.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from kifs.clients.kishin import KifNotAvailable, KishinAnalyticsClient
from kifs.config import Settings
from kifs.kif.parser import parse_kif
from kifs.storage.database import KifuDatabase, isoformat, utcnow

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


async def backfill_ranks(db: KifuDatabase, settings: Settings,
                         limit: Optional[int] = None) -> dict:
    # One query rather than streaming every document through Python.
    todo = [
        row["game_id"] for row in db.connection.execute(
            "SELECT game_id FROM games WHERE sente_rank IS NULL AND gote_rank IS NULL")
    ]
    if limit:
        todo = todo[:limit]
    log.info("Rank backfill: %d games need a re-download.", len(todo))
    counts = {"processed": 0, "updated": 0, "unavailable": 0}
    if not todo:
        return counts

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers, timeout=settings.request_timeout) as client:
        kishin = KishinAnalyticsClient(client, settings.analytics_session, settings.web_session)
        if not kishin.authenticated:
            log.error("ANALYTICS_SESSION missing — cannot download. Aborting.")
            return counts

        for game_id in todo:
            counts["processed"] += 1
            try:
                kif_text = await kishin.fetch_kif(game_id)
            except (KifNotAvailable, httpx.HTTPError) as exc:
                counts["unavailable"] += 1
                log.debug("backfill %s: %s", game_id, exc)
                await asyncio.sleep(settings.request_delay)
                continue

            path = settings.kif_path(game_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(kif_text, encoding="utf-8")
            parsed = parse_kif(path)
            if parsed and (parsed.sente_rank or parsed.gote_rank):
                document = parsed.to_document(game_id)
                document["rank_backfilled_at"] = isoformat(utcnow())
                db.upsert_game(document)
                counts["updated"] += 1

            if counts["processed"] % 100 == 0:
                db.flush(force=True)
                log.info("  [%d/%d] updated=%d unavailable=%d",
                         counts["processed"], len(todo), counts["updated"],
                         counts["unavailable"])
            await asyncio.sleep(settings.request_delay)

    db.flush(force=True)
    log.info("Backfill done: %(processed)d processed, %(updated)d gained a rank, "
             "%(unavailable)d unavailable.", counts)
    return counts
