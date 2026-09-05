"""Client for the Kishin Analytics KIF API.

The site is an SPA; this REST endpoint returns ``{"game_id": ..., "kif": ...}``
and the KIF text embeds 先手段級/後手段級 (rank at game time). Auth is the Django
``sessionid`` cookie — *not* the Rails ``_web_session``.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
import orjson

log = logging.getLogger(__name__)

KIF_API_URL = "https://kishin-analytics.heroz.jp/kifu/api/shogi-wars/games/{game_id}/kif/"


class KifNotAvailable(Exception):
    """The game exists but its KIF is not published (yet). Retry later."""


class KishinAnalyticsClient:
    def __init__(self, client: httpx.AsyncClient, analytics_session: Optional[str],
                 web_session: Optional[str] = None):
        self._client = client
        cookies = []
        if analytics_session:
            cookies.append(f"sessionid={analytics_session}")
        if web_session:
            cookies.append(f"_web_session={web_session}")
        self._cookie = "; ".join(cookies)

    @property
    def authenticated(self) -> bool:
        return "sessionid=" in self._cookie

    async def fetch_kif(self, game_id: str) -> str:
        """Return the KIF text, or raise :class:`KifNotAvailable`.

        A 404 means "not published yet" far more often than "never will be"
        (games become downloadable some time after they finish), so the caller
        schedules a retry instead of dropping the game — see issue #3.
        """
        headers = {"Accept": "application/json"}
        if self._cookie:
            headers["Cookie"] = self._cookie

        response = await self._client.get(KIF_API_URL.format(game_id=game_id), headers=headers)
        if response.status_code == 404:
            raise KifNotAvailable(f"{game_id}: KIF not published yet (404)")
        response.raise_for_status()
        kif = orjson.loads(response.content).get("kif")
        if not kif:
            raise KifNotAvailable(f"{game_id}: response carried no kif field")
        return kif
