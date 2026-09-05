"""Find users and their game ids, and feed both into the frontier / database."""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Tuple

from kifs.clients.shogiwars import ShogiWarsClient, players_from_game_id
from kifs.config import Settings
from kifs.storage.database import KifuDatabase
from kifs.storage.frontier import Frontier

log = logging.getLogger(__name__)


class Discovery:
    def __init__(self, client: ShogiWarsClient, db: KifuDatabase,
                 frontier: Frontier, settings: Settings):
        self._client = client
        self._db = db
        self._frontier = frontier
        self._settings = settings

    async def seed_from_rankings(self, offsets: Optional[List[int]] = None) -> int:
        """Top up the frontier from the event ranking.

        Called at startup and again whenever the frontier runs dry, which is
        what keeps the service from exiting when the queue empties (issue #2).
        """
        offsets = offsets or self._settings.seed_offsets
        added = 0
        for offset in offsets:
            user_ids = await self._client.fetch_ranking_user_ids(offset)
            added += self._frontier.add_many(user_ids)
            await asyncio.sleep(self._settings.request_delay)
        log.info("Seeded %d new users from %d ranking offsets.", added, len(offsets))
        self._frontier.save(force=True)
        return added

    async def crawl_user(self, user_id: str) -> Tuple[List[Tuple[str, str]], int]:
        """Walk a user's history and register every game found.

        Returns ``(games, new_count)`` where ``games`` is a list of
        ``(game_id, game_type)`` — every game seen, not only the new ones, so
        the caller can re-check games whose KIF was not published last time.

        Paging stops when a page yields no game id that is new *to this walk*,
        rather than after a hard-coded three pages (issue #3).
        """
        games: List[Tuple[str, str]] = []
        new_count = 0
        seen_this_walk: set[str] = set()

        for game_type in self._settings.game_types:
            for page in range(1, self._settings.max_history_pages + 1):
                game_ids = await self._client.fetch_game_ids(user_id, game_type, page=page)
                await asyncio.sleep(self._settings.request_delay)
                if not game_ids:
                    break

                page_new = 0
                for game_id in game_ids:
                    if game_id in seen_this_walk:
                        continue
                    seen_this_walk.add(game_id)
                    page_new += 1
                    games.append((game_id, game_type))
                    if self._db.add_record(game_id, game_type, user_id):
                        new_count += 1
                    # Both players become crawl candidates (graph traversal).
                    for player in players_from_game_id(game_id):
                        if player:
                            self._frontier.add(player)

                if page_new == 0:
                    break

        return games, new_count
