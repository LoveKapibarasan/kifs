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

    clauses: List[str] = []
    params: List = []
    if player:
        clauses.append("(sente LIKE ? OR gote LIKE ?)")
        params += [f"%{player}%", f"%{player}%"]
    if sente:
        clauses.append("sente LIKE ?")
        params.append(f"%{sente}%")
    if gote:
        clauses.append("gote LIKE ?")
        params.append(f"%{gote}%")
    if result:
        clauses.append("result = ?")
        params.append(result)
    if min_moves is not None:
        clauses.append("total_moves >= ?")
        params.append(min_moves)

    query = "SELECT * FROM games"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " LIMIT ?"
    params.append(limit)

    from kifs.storage.database import _row_to_game

    return [_row_to_game(row) for row in db.connection.execute(query, params)]


def summarize(db: KifuDatabase) -> Dict:
    """Aggregate counts for ``kifs stats``.

    Aggregation happens in SQLite; the previous version streamed every document
    into Python to count them.
    """
    connection = db.connection
    totals = connection.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_moves), 0) AS moves, "
        "       SUM(CASE WHEN sente_rank IS NOT NULL OR gote_rank IS NOT NULL "
        "                THEN 1 ELSE 0 END) AS with_rank "
        "FROM games").fetchone()
    total = totals["n"]

    results = {row["result"]: row["n"] for row in connection.execute(
        "SELECT result, COUNT(*) AS n FROM games WHERE result IS NOT NULL "
        "GROUP BY result ORDER BY n DESC")}

    unique_players = connection.execute(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT sente AS name FROM games WHERE sente IS NOT NULL "
        "  UNION SELECT gote FROM games WHERE gote IS NOT NULL)").fetchone()["n"]

    top_players = [(row["name"], row["n"]) for row in connection.execute(
        "SELECT name, COUNT(*) AS n FROM ("
        "  SELECT sente AS name FROM games WHERE sente IS NOT NULL "
        "  UNION ALL SELECT gote FROM games WHERE gote IS NOT NULL) "
        "GROUP BY name ORDER BY n DESC LIMIT 5")]

    return {
        "total_games": total,
        "unique_players": unique_players,
        "average_moves": (totals["moves"] / total) if total else 0.0,
        "games_with_rank": totals["with_rank"] or 0,
        "results": results,
        "top_players": top_players,
        "crawl_status": db.status_counts(),
    }
