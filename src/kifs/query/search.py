"""Search and statistics over the indexed games."""
from __future__ import annotations

from typing import Dict, List, Optional

from kifs.storage.database import KifuDatabase


def search_games(db: KifuDatabase, game_id: Optional[str] = None,
                 player: Optional[str] = None, sente: Optional[str] = None,
                 gote: Optional[str] = None, result: Optional[str] = None,
                 min_moves: Optional[int] = None, limit: int = 10) -> List[dict]:
    """Filter indexed games. A ``game_id`` lookup is O(1); the rest scan."""
    if game_id:
        found = db.get_game(game_id)
        return [found] if found else []

    def matches(game: dict) -> bool:
        if player:
            needle = player.lower()
            names = (game.get("sente") or "", game.get("gote") or "")
            if not any(needle in name.lower() for name in names):
                return False
        if sente and sente.lower() not in (game.get("sente") or "").lower():
            return False
        if gote and gote.lower() not in (game.get("gote") or "").lower():
            return False
        if result and game.get("result") != result:
            return False
        if min_moves is not None and game.get("total_moves", 0) < min_moves:
            return False
        return True

    hits: List[dict] = []
    for game in db.games():
        if matches(game):
            hits.append(game)
            if len(hits) >= limit:
                break
    return hits


def summarize(db: KifuDatabase) -> Dict:
    """Aggregate counts for ``kifs stats``."""
    total = 0
    total_moves = 0
    players: Dict[str, int] = {}
    results: Dict[str, int] = {}
    with_rank = 0

    for game in db.games():
        total += 1
        total_moves += game.get("total_moves", 0)
        for name in (game.get("sente"), game.get("gote")):
            if name:
                players[name] = players.get(name, 0) + 1
        outcome = game.get("result")
        if outcome:
            results[outcome] = results.get(outcome, 0) + 1
        if game.get("sente_rank") or game.get("gote_rank"):
            with_rank += 1

    return {
        "total_games": total,
        "unique_players": len(players),
        "average_moves": (total_moves / total) if total else 0.0,
        "games_with_rank": with_rank,
        "results": dict(sorted(results.items(), key=lambda kv: kv[1], reverse=True)),
        "top_players": sorted(players.items(), key=lambda kv: kv[1], reverse=True)[:5],
        "crawl_status": db.status_counts(),
    }
