"""Annotate games with the players' *current* rank scraped from mypage.

The rank embedded in a KIF (先手段級) is the rank *at game time* and is the
better value; this pass only fills the gap for games whose KIF predates the
analytics API. See :mod:`kifs.ranks.backfill` for re-fetching those KIFs.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import httpx
import orjson

from kifs.clients.shogiwars import ShogiWarsClient
from kifs.config import Settings
from kifs.storage.database import KifuDatabase

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
FLUSH_EVERY = 25


def ranks_path(settings: Settings) -> Path:
    return settings.state_dir / "user_ranks.json"


def load_ranks(settings: Settings) -> Dict[str, dict]:
    path = ranks_path(settings)
    if path.is_file() and path.stat().st_size > 0:
        return orjson.loads(path.read_bytes())
    return {}


def save_ranks(settings: Settings, ranks: Dict[str, dict]) -> None:
    path = ranks_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(orjson.dumps(ranks))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def distinct_players(db: KifuDatabase) -> List[str]:
    names = set()
    for game in db.games():
        for key in ("sente", "gote"):
            if game.get(key):
                names.add(game[key])
    return sorted(names)


async def fetch_ranks(db: KifuDatabase, settings: Settings) -> Dict[str, dict]:
    """Fetch and cache the current rank of every player in the database."""
    ranks = load_ranks(settings)
    todo = [name for name in distinct_players(db) if name not in ranks]
    log.info("Rank fetch: %d players cached, %d to fetch.", len(ranks), len(todo))
    if not todo:
        return ranks

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers, timeout=settings.request_timeout) as client:
        wars = ShogiWarsClient(client, settings.web_session)
        for index, user_id in enumerate(todo, start=1):
            info = await wars.fetch_user_rank(user_id)
            ranks[user_id] = info or {
                "user_id": user_id, "rank_3m": None, "rating_3m": None,
                "highest_3m": None, "modes": {}, "error": True,
            }
            ranks[user_id]["fetched_at"] = datetime.now(timezone.utc).isoformat()
            if index % FLUSH_EVERY == 0:
                save_ranks(settings, ranks)
                log.info("  [%d/%d] %s -> %s", index, len(todo), user_id,
                         ranks[user_id].get("rank_3m"))
            await asyncio.sleep(settings.request_delay)

    save_ranks(settings, ranks)
    return ranks


def annotate_games(db: KifuDatabase, settings: Settings) -> int:
    """Write cached ranks onto games that have none from their KIF."""
    ranks = load_ranks(settings)
    if not ranks:
        log.warning("No cached ranks; run `kifs ranks fetch` first.")
        return 0

    annotated = 0
    # Collect first, then write: mutating while iterating a live cursor is not
    # safe, and this way the writes go out in batched transactions.
    updates = []
    for game in db.games():
        game_id = game.get("game_id")
        if not game_id:
            continue
        patch = {"game_id": game_id}
        for side in ("sente", "gote"):
            info = ranks.get(game.get(side)) or {}
            if info.get("rating_3m") is not None and game.get(f"{side}_rating") is None:
                patch[f"{side}_rating"] = info["rating_3m"]
            # Never overwrite the rank the KIF recorded at game time.
            if not game.get(f"{side}_rank") and info.get("rank_3m"):
                patch[f"{side}_rank"] = info["rank_3m"]
                patch[f"{side}_rank_is_current"] = True
        if len(patch) > 1:
            updates.append(patch)

    for patch in updates:
        db.upsert_game(patch)
        annotated += 1
    if annotated:
        db.flush(force=True)
    log.info("Annotated %d games with cached ranks.", annotated)
    return annotated
