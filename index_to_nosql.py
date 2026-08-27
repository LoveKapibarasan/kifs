#!/usr/bin/env python3
import os
import glob
import argparse
from datetime import datetime
import orjson
from tinydb import TinyDB, Query
from tinydb.storages import Storage

CRAWL_RECORDS_TABLE = "crawl_records"
STATUS_KIF_MISSING = "kif_missing"
STATUS_KIF_DOWNLOADED = "kif_downloaded"
STATUS_INDEXED = "indexed"


class OrJSONStorage(Storage):
    def __init__(self, filename, **kwargs):
        self.filename = filename

    def read(self):
        if not os.path.exists(self.filename) or os.path.getsize(self.filename) == 0:
            return None
        with open(self.filename, 'rb') as f:
            return orjson.loads(f.read())

    def write(self, data):
        # Write atomically (temp file -> fsync -> rename) so an interrupted
        # write (e.g. process killed mid-dump) cannot truncate/corrupt the DB.
        tmp = f"{self.filename}.tmp"
        with open(tmp, 'wb') as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.filename)

    def close(self):
        pass


def parse_kif(file_path: str):
    """
    Parses a Shogi KIF file and extracts headers, moves, result, and total moves.
    """
    if not os.path.exists(file_path):
        return None

    headers = {}
    moves = []
    result = None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        # Fallback to shift_jis if utf-8 fails
        try:
            with open(file_path, "r", encoding="shift_jis") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            return None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Parse header lines (key：value)
        if "：" in line and not line[0].isdigit():
            parts = line.split("：", 1)
            if len(parts) == 2:
                key, val = parts[0].strip(), parts[1].strip()
                headers[key] = val
                continue

        # Parse moves / result lines
        # Format: "1 ２六歩(27)" or "85 投了"
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            move_num = int(parts[0])
            move_content = parts[1].strip()

            # Identify termination/result terms
            if move_content in ["投了", "中断", "持将棋", "千日手", "合意", "切れ負け", "反則手", "時間切れ", "反則勝ち", "詰み", "不戦勝", "不戦敗"]:
                result = move_content
            else:
                moves.append(move_content)
        elif line in ["投了", "中断", "持将棋", "千日手", "合意", "切れ負け", "反則手", "時間切れ", "反則勝ち", "詰み", "不戦勝", "不戦敗"]:
            result = line

    # Map standard headers to English keys for consistent querying
    sente = headers.get("先手") or headers.get("下手")
    gote = headers.get("後手") or headers.get("上手")
    start_time = headers.get("開始日時")
    end_time = headers.get("終了日時")
    location = headers.get("場所")
    handicap = headers.get("手合割")
    # Rank at game time, present in KIFs from the new analytics API (else None).
    sente_rank = headers.get("先手段級") or headers.get("下手段級")
    gote_rank = headers.get("後手段級") or headers.get("上手段級")

    return {
        "sente": sente,
        "gote": gote,
        "sente_rank": sente_rank,
        "gote_rank": gote_rank,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "handicap": handicap,
        "result": result,
        "moves": moves,
        "total_moves": len(moves),
        "raw_headers": headers
    }

def index_games(db_path: str, kif_dir: str):
    """
    Scans KIF files, then indexes them in TinyDB.
    """
    print(f"[*] Initializing database at: {db_path}")
    db = TinyDB(db_path, storage=OrJSONStorage)
    
    # Load existing game IDs to avoid duplicates
    existing_ids = {doc["game_id"] for doc in db.all() if "game_id" in doc}
    print(f"[*] Found {len(existing_ids)} games already indexed in the database.")

    kif_pattern = os.path.join(kif_dir, "*.kif")
    kif_files = glob.glob(kif_pattern)
    print(f"[*] Found {len(kif_files)} KIF files in {kif_dir}.")

    new_docs = []
    processed_count = 0
    skipped_count = 0

    for kif_file in kif_files:
        filename = os.path.basename(kif_file)
        game_id = os.path.splitext(filename)[0]

        if game_id in existing_ids:
            skipped_count += 1
            continue

        parsed_data = parse_kif(kif_file)
        if not parsed_data:
            continue

        doc = {
            "game_id": game_id,
            "sente": parsed_data["sente"],
            "gote": parsed_data["gote"],
            "sente_rank": parsed_data["sente_rank"],
            "gote_rank": parsed_data["gote_rank"],
            "start_time": parsed_data["start_time"],
            "end_time": parsed_data["end_time"],
            "location": parsed_data["location"],
            "handicap": parsed_data["handicap"],
            "result": parsed_data["result"],
            "moves": parsed_data["moves"],
            "total_moves": parsed_data["total_moves"],
            "raw_headers": parsed_data["raw_headers"],
            "crawler_user": None,
            "crawler_type": None,
            "crawler_ts": None
        }

        new_docs.append(doc)
        processed_count += 1

        if processed_count % 1000 == 0:
            print(f"[*] Parsed {processed_count} files...")

    # 3. Batch insert new documents
    if new_docs:
        print(f"[*] Inserting {len(new_docs)} new games into database...")
        db.insert_multiple(new_docs)
        print("[*] Insertion complete.")
    else:
        print("[*] No new games to index.")

    print(f"[+] Indexing completed: {processed_count} indexed, {skipped_count} skipped (already in database). Total database count: {len(db)}")
    sync_crawl_status(db_path, kif_dir)

def sync_crawl_status(db_path: str, kif_dir: str):
    """
    Reconciles crawl_records with local KIF files and indexed game documents.
    """
    db = TinyDB(db_path, storage=OrJSONStorage)
    crawl_records = db.table(CRAWL_RECORDS_TABLE)
    records = crawl_records.all()
    if not records:
        print("[*] No crawl records to sync.")
        return

    indexed_ids = {doc["game_id"] for doc in db.table("_default").all() if "game_id" in doc}
    synced_at = datetime.now().isoformat()
    updated_records = []
    counts = {}

    for record in records:
        game_id = record.get("game_id")
        if not game_id:
            continue

        has_kif_file = os.path.exists(os.path.join(kif_dir, f"{game_id}.kif"))
        if game_id in indexed_ids:
            status = STATUS_INDEXED
        elif has_kif_file:
            status = STATUS_KIF_DOWNLOADED
        else:
            status = STATUS_KIF_MISSING

        updated = dict(record)
        updated["kif_status"] = status
        updated["has_kif_file"] = has_kif_file
        updated["is_indexed"] = game_id in indexed_ids
        updated["status_synced_at"] = synced_at
        updated_records.append(updated)
        counts[status] = counts.get(status, 0) + 1

    crawl_records.truncate()
    if updated_records:
        crawl_records.insert_multiple(updated_records)

    print("[+] Crawl status synced:")
    for status, count in sorted(counts.items()):
        print(f"    {status}: {count}")

def search_games(db_path: str, game_id: str = None, player: str = None, sente: str = None, gote: str = None, result: str = None, min_moves: int = None, limit: int = 10):
    """
    Search games inside the TinyDB database. Optimized for fast GameID and Player search.
    """
    import time
    start_time = time.time()
    
    db = TinyDB(db_path, storage=OrJSONStorage)
    results = []

    if game_id:
        # Fast lookup by unique game_id (O(1) search)
        Game = Query()
        r = db.get(Game.game_id == game_id)
        if r:
            results = [r]
    else:
        # Load all records for filtering
        results = db.all()
        
        if player:
            player_lower = player.lower()
            results = [r for r in results if (r.get("sente") and player_lower in r["sente"].lower()) or (r.get("gote") and player_lower in r["gote"].lower())]
        if sente:
            sente_lower = sente.lower()
            results = [r for r in results if r.get("sente") and sente_lower in r["sente"].lower()]
        if gote:
            gote_lower = gote.lower()
            results = [r for r in results if r.get("gote") and gote_lower in r["gote"].lower()]
        if result:
            results = [r for r in results if r.get("result") == result]
        if min_moves is not None:
            results = [r for r in results if r.get("total_moves", 0) >= min_moves]

    elapsed = time.time() - start_time
    print(f"\n[+] Found {len(results)} matching games in {elapsed:.6f} seconds.")
    
    # Display results
    for i, r in enumerate(results[:limit]):
        print(f"\n[{i+1}] Game ID: {r.get('game_id')}")
        print(f"    Sente: {r.get('sente')} vs Gote: {r.get('gote')}")
        print(f"    Start Time: {r.get('start_time')} | Result: {r.get('result')}")
        print(f"    Total Moves: {r.get('total_moves')} | Location: {r.get('location')}")

    if len(results) > limit:
        print(f"\n... and {len(results) - limit} more games.")

def print_db_stats(db_path: str):
    """
    Analyzes and prints overall statistics of the indexed shogi database.
    """
    db = TinyDB(db_path, storage=OrJSONStorage)
    games = db.all()
    total_games = len(games)
    
    if total_games == 0:
        print("Database is empty.")
        return
        
    players = set()
    results = {}
    total_moves = 0
    player_games = {}
    
    for g in games:
        s = g.get("sente")
        go = g.get("gote")
        if s:
            players.add(s)
            player_games[s] = player_games.get(s, 0) + 1
        if go:
            players.add(go)
            player_games[go] = player_games.get(go, 0) + 1
            
        r = g.get("result")
        if r:
            results[r] = results.get(r, 0) + 1
            
        total_moves += g.get("total_moves", 0)
        
    avg_moves = total_moves / total_games
    sorted_players = sorted(player_games.items(), key=lambda x: x[1], reverse=True)
    
    print("\n=== Database Statistics ===")
    print(f"Total Games Indexed: {total_games}")
    print(f"Total Unique Players: {len(players)}")
    print(f"Average Moves per Game: {avg_moves:.1f}")
    
    print("\n--- Game Result Distribution ---")
    for r, count in sorted(results.items(), key=lambda x: x[1], reverse=True):
        print(f"  {r}: {count} ({count/total_games*100:.1f}%)")
        
    print("\n--- Top 5 Most Active Players ---")
    for name, count in sorted_players[:5]:
        print(f"  {name}: {count} games")

    crawl_records = db.table(CRAWL_RECORDS_TABLE).all()
    if crawl_records:
        status_counts = {}
        for record in crawl_records:
            status = record.get("kif_status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        print("\n--- Crawl Record Status ---")
        print(f"  Total Crawl Records: {len(crawl_records)}")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")

def main():
    parser = argparse.ArgumentParser(description="Index Shogi KIF records into TinyDB NoSQL database.")
    parser.add_argument("--db", type=str, default="kifu_db.json", help="Path to the TinyDB JSON database file.")
    parser.add_argument("--kif-dir", type=str, default="kif_data", help="Directory containing raw KIF files.")
    
    # Actions
    parser.add_argument("--index", action="store_true", help="Perform indexing of new KIF files.")
    parser.add_argument("--search", action="store_true", help="Search the indexed database.")
    parser.add_argument("--stats", action="store_true", help="Show database statistics.")
    parser.add_argument("--sync-crawl-status", action="store_true", help="Mark crawl records as indexed, downloaded, or KIF missing.")
    
    # Search parameters
    parser.add_argument("--game-id", type=str, help="Search by unique Game ID.")
    parser.add_argument("--player", type=str, help="Search by player name (either sente or gote).")
    parser.add_argument("--sente", type=str, help="Search by Sente player name.")
    parser.add_argument("--gote", type=str, help="Search by Gote player name.")
    parser.add_argument("--result", type=str, help="Search by result (e.g. 投了, 千日手, 切れ負け).")
    parser.add_argument("--min-moves", type=int, help="Search for games with at least this number of moves.")
    parser.add_argument("--limit", type=int, default=10, help="Limit search results displayed (default: 10).")

    args = parser.parse_args()

    if args.index:
        index_games(args.db, args.kif_dir)
    elif args.sync_crawl_status:
        sync_crawl_status(args.db, args.kif_dir)
    elif args.search:
        search_games(
            args.db, 
            game_id=args.game_id,
            player=args.player, 
            sente=args.sente, 
            gote=args.gote, 
            result=args.result, 
            min_moves=args.min_moves,
            limit=args.limit
        )
    elif args.stats:
        print_db_stats(args.db)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
