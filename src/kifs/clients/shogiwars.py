"""Client for shogiwars.heroz.jp (Rails, HTML responses).

Provides the three read paths the pipeline needs: the event ranking (to seed
users), a user's game history (to discover game ids) and a user's mypage (to
read their current dan/kyu rank).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://shogiwars.heroz.jp"
RANKING_URL = f"{BASE_URL}/events/point2026"
HISTORY_URL = f"{BASE_URL}/games/history"
MYPAGE_URL = f"{BASE_URL}/users/mypage/{{user_id}}"

_USER_ID_RE = re.compile(r'/users/mypage/([^?"]+)')
_GAME_ID_RE = re.compile(r'wars_game_id=([^&"]+)')

# A <tr> inside #user_dankyu: mode label, current dan/kyu, then a cell that may
# carry "最高: <rank>" and a "<rating>段" monthly-rating link.
_TABLE_RE = re.compile(r'<table id="user_dankyu">(.*?)</table>', re.DOTALL)
_ROW_RE = re.compile(
    r'<th[^>]*>(?P<mode>[^<]+)</th>\s*'
    r'<td class="dankyu">(?P<dankyu>[^<]+)</td>'
    r'(?P<rest>.*?)(?=<tr|\Z)',
    re.DOTALL,
)
_HIGHEST_RE = re.compile(r'最高:\s*([^\s&<]+)')
_RATING_RE = re.compile(r'>([0-9.]+)段[^<]*</a>')

_MODE_KEY = {"10分": "10m", "3分": "3m", "10秒": "10s", "スプリント": "sprint", "詰めバト": "tsume"}


class AuthenticationError(RuntimeError):
    """Raised when the session cookie is rejected, i.e. it has expired."""


class ShogiWarsClient:
    def __init__(self, client: httpx.AsyncClient, web_session: Optional[str]):
        self._client = client
        self._web_session = web_session

    @property
    def cookie_header(self) -> Dict[str, str]:
        if not self._web_session:
            return {}
        return {"Cookie": f"_web_session={self._web_session}"}

    async def fetch_ranking_user_ids(self, offset: int) -> List[str]:
        """User ids from one page of the event ranking, in page order."""
        params = {"locale": "ja", "rank_criteria": "point", "start": offset}
        try:
            response = await self._client.get(RANKING_URL, params=params, headers=self.cookie_header)
            if response.status_code in (401, 403):
                raise AuthenticationError(f"ranking returned {response.status_code}")
            response.raise_for_status()
        except AuthenticationError:
            raise
        except Exception as exc:
            log.warning("Ranking fetch failed at offset %s: %s", offset, exc)
            return []
        return list(dict.fromkeys(_USER_ID_RE.findall(response.text)))

    async def fetch_game_ids(self, user_id: str, game_type: str, page: int = 1) -> List[str]:
        """Game ids from one page of a user's history for a given game type."""
        params = {
            "gtype": game_type,
            "user_id": user_id,
            "locale": "ja",
            "page": page,
            "init_pos_type": "normal",
        }
        try:
            response = await self._client.get(HISTORY_URL, params=params, headers=self.cookie_header)
            if response.status_code in (401, 403):
                raise AuthenticationError(f"history returned {response.status_code}")
            response.raise_for_status()
        except AuthenticationError:
            raise
        except Exception as exc:
            log.warning("History fetch failed for %s (%s p%s): %s", user_id, game_type, page, exc)
            return []
        return list(dict.fromkeys(_GAME_ID_RE.findall(response.text)))

    async def fetch_user_rank(self, user_id: str) -> Optional[dict]:
        """The user's *current* rank per mode — not their rank at game time."""
        try:
            response = await self._client.get(
                MYPAGE_URL.format(user_id=user_id),
                params={"locale": "ja"},
                headers=self.cookie_header,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception as exc:
            log.warning("Rank fetch failed for %s: %s", user_id, exc)
            return None

        modes = parse_mypage(response.text)
        if not modes:
            return None
        three_minute = modes.get("3m", {})
        return {
            "user_id": user_id,
            "rank_3m": three_minute.get("dankyu"),
            "rating_3m": three_minute.get("rating"),
            "highest_3m": three_minute.get("highest"),
            "modes": modes,
        }


def parse_mypage(html: str) -> Dict[str, dict]:
    """Return ``{mode_key: {dankyu, rating, highest}}`` parsed from mypage HTML."""
    out: Dict[str, dict] = {}
    table = _TABLE_RE.search(html)
    if not table:
        return out
    for row in _ROW_RE.finditer(table.group(1)):
        mode = row.group("mode").strip()
        rest = row.group("rest")
        highest = _HIGHEST_RE.search(rest)
        rating = _RATING_RE.search(rest)
        out[_MODE_KEY.get(mode, mode)] = {
            "dankyu": row.group("dankyu").strip(),
            "rating": float(rating.group(1)) if rating else None,
            "highest": highest.group(1).strip() if highest else None,
        }
    return out


def players_from_game_id(game_id: str) -> tuple[Optional[str], Optional[str]]:
    """Split ``sente-gote-YYYYMMDD_HHMMSS`` into its two player ids.

    Used to grow the crawl frontier without an extra request. Player ids may
    themselves contain ``-``, so only the trailing timestamp is stripped and the
    remainder is split once; ambiguous ids simply yield ``(None, None)``.
    """
    head, _, tail = game_id.rpartition("-")
    if not head or not tail:
        return None, None
    first, separator, second = head.partition("-")
    if not separator:
        return None, None
    return first or None, second or None
