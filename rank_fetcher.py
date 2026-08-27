#!/usr/bin/env python3
"""Fetch a Shogi Wars user's current dan/kyu rank from their public mypage.

The KIF files themselves contain only usernames (no rank), so rank has to be
scraped separately. The mypage table `#user_dankyu` lists the current rank per
game mode (10分 / 3分 / 10秒 / スプリント / 詰めバト). This pipeline crawls the
3-minute (gtype=sb) ladder, so `rank_3m` is the relevant value.

Note: this is the user's *current* rank, not their rank at the time of a game.
"""
import re
from typing import Dict, Optional
import httpx

MYPAGE_URL = "https://shogiwars.heroz.jp/users/mypage/{user_id}"

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

# Map the Japanese mode label to a short key.
_MODE_KEY = {"10分": "10m", "3分": "3m", "10秒": "10s", "スプリント": "sprint", "詰めバト": "tsume"}


def parse_mypage(html: str) -> Dict[str, dict]:
    """Return {mode_key: {dankyu, rating, highest}} parsed from mypage HTML."""
    out: Dict[str, dict] = {}
    m = _TABLE_RE.search(html)
    if not m:
        return out
    block = m.group(1)
    for row in _ROW_RE.finditer(block):
        mode = row.group("mode").strip()
        key = _MODE_KEY.get(mode, mode)
        rest = row.group("rest")
        highest = _HIGHEST_RE.search(rest)
        rating = _RATING_RE.search(rest)
        out[key] = {
            "dankyu": row.group("dankyu").strip(),
            "rating": float(rating.group(1)) if rating else None,
            "highest": highest.group(1).strip() if highest else None,
        }
    return out


async def fetch_user_rank(client: httpx.AsyncClient, user_id: str) -> Optional[dict]:
    """Fetch and parse a user's rank. Returns None on HTTP/parse failure."""
    try:
        r = await client.get(MYPAGE_URL.format(user_id=user_id), params={"locale": "ja"})
        r.raise_for_status()
    except Exception as e:
        print(f"  [!] rank fetch failed for {user_id}: {e}")
        return None
    modes = parse_mypage(r.text)
    if not modes:
        return None
    three = modes.get("3m", {})
    return {
        "user_id": user_id,
        "rank_3m": three.get("dankyu"),
        "rating_3m": three.get("rating"),
        "highest_3m": three.get("highest"),
        "modes": modes,
    }


if __name__ == "__main__":
    import asyncio, os, json
    from dotenv import load_dotenv
    load_dotenv()
    ws = os.getenv("WEB_SESSION")
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": f"_web_session={ws}"}

    async def _demo():
        async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as c:
            for u in ["takachang2", "champion2020", "ahitak"]:
                print(json.dumps(await fetch_user_rank(c, u), ensure_ascii=False))
                await asyncio.sleep(1.0)

    asyncio.run(_demo())
