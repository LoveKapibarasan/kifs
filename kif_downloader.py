import os
import asyncio
from datetime import datetime
from typing import Optional
import httpx
import orjson
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

# Kishin Analytics REST API (the site is now an SPA). This endpoint returns
# {"game_id": ..., "kif": ...} and the KIF includes 先手段級/後手段級 (rank at
# game time). Auth is the Django `sessionid` cookie, NOT the Rails _web_session.
KIF_API_URL = "https://kishin-analytics.heroz.jp/kifu/api/shogi-wars/games/{game_id}/kif/"


class SwarsKifDownloader:
    def __init__(self):
        self.web_session = os.getenv("WEB_SESSION")
        self.analytics_session = os.getenv("ANALYTICS_SESSION")
        # NB: no global Accept header — the shared client also hits Rails HTML
        # endpoints (games/history) which return 406 for Accept: application/json.
        # The JSON Accept is sent per-request in fetch_kif instead.
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        # The KIF API authenticates with the analytics `sessionid` cookie.
        cookies = []
        if self.analytics_session:
            cookies.append(f"sessionid={self.analytics_session}")
        if self.web_session:
            cookies.append(f"_web_session={self.web_session}")
        if cookies:
            self.headers["Cookie"] = "; ".join(cookies)

    async def fetch_kif(self, client: httpx.AsyncClient, game_id: str) -> Optional[str]:
        """Fetch the KIF text for a game via the Kishin Analytics REST API."""
        try:
            response = await client.get(KIF_API_URL.format(game_id=game_id),
                                        headers={"Accept": "application/json"})
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = orjson.loads(response.content)
            return data.get("kif")
        except Exception as e:
            print(f"  [!] Error during download of {game_id}: {e}")
            return None

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download KIF files by game ID and index them into TinyDB.")
    parser.add_argument("game_ids", nargs="*", help="Shogi Wars game IDs to download.")
    parser.add_argument("--db", default="kifu_db.json", help="TinyDB database path.")
    parser.add_argument("--kif-dir", default="kif_data", help="Directory to save KIF files.")
    parser.add_argument("--game-type", default=None, help="Optional game type metadata, e.g. sb or s1.")
    parser.add_argument("--source-user", default=None, help="Optional crawler/source user metadata.")
    parser.add_argument("--missing", action="store_true", help="Download records marked as KIF missing in crawl_records.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of missing records to process.")
    args = parser.parse_args()

    if not args.game_ids and not args.missing:
        parser.error("provide game_ids or use --missing")

    save_dir = args.kif_dir
    
    # Create output directory if it doesn't exist
    os.makedirs(save_dir, exist_ok=True)

    downloader = SwarsKifDownloader()
    
    # Initialize TinyDB
    from tinydb import TinyDB, Query
    from index_to_nosql import (
        CRAWL_RECORDS_TABLE,
        OrJSONStorage,
        STATUS_INDEXED,
        STATUS_KIF_DOWNLOADED,
        STATUS_KIF_MISSING,
    )
    db = TinyDB(args.db, storage=OrJSONStorage)
    crawl_records = db.table(CRAWL_RECORDS_TABLE)
    Game = Query()

    if args.missing:
        indexed_ids = {doc["game_id"] for doc in db.table("_default").all() if "game_id" in doc}
        game_ids = []
        for record in crawl_records.all():
            game_id = record.get("game_id")
            if not game_id or game_id in indexed_ids:
                continue
            if record.get("kif_status") in (None, STATUS_KIF_MISSING):
                game_ids.append(game_id)
            elif not os.path.exists(os.path.join(save_dir, f"{game_id}.kif")):
                game_ids.append(game_id)
        if args.limit is not None:
            game_ids = game_ids[:args.limit]
        print(f"[*] Missing KIF targets: {len(game_ids)}")
    else:
        game_ids = args.game_ids
    
    async with httpx.AsyncClient(headers=downloader.headers, timeout=20.0) as client:
        for game_id in game_ids:
            file_path = os.path.join(save_dir, f"{game_id}.kif")
            source_record = crawl_records.get(Game.game_id == game_id) or {}
            game_type = args.game_type or source_record.get("game_type")
            source_user = args.source_user or source_record.get("source_user")

            if os.path.exists(file_path):
                print(f"[*] Already exists: {file_path}")
            else:
                print(f"[*] Fetching KIF for: {game_id}")
                kif_text = await downloader.fetch_kif(client, game_id)
                if not kif_text:
                    print(f"  [-] KIF not available yet: {game_id}")
                    crawl_records.update({
                        "kif_status": STATUS_KIF_MISSING,
                        "has_kif_file": False,
                        "is_indexed": False,
                        "last_attempt_at": datetime.now().isoformat(),
                        "last_error": "KIF not found in analytics response"
                    }, Game.game_id == game_id)
                    await asyncio.sleep(1.5)
                    continue

                with open(file_path, "w", encoding="utf-8") as out:
                    out.write(kif_text)
                print(f"  [+] Saved: {file_path}")
                crawl_records.update({
                    "kif_status": STATUS_KIF_DOWNLOADED,
                    "has_kif_file": True,
                    "last_attempt_at": datetime.now().isoformat(),
                    "last_error": None
                }, Game.game_id == game_id)

            try:
                from index_to_nosql import parse_kif
                parsed_data = parse_kif(file_path)
                if parsed_data:
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
                        "crawler_user": source_user,
                        "crawler_type": game_type,
                        "crawler_ts": source_record.get("discovered_at")
                    }
                    db.upsert(doc, Game.game_id == game_id)
                    crawl_records.upsert({
                        "game_id": game_id,
                        "game_type": game_type,
                        "source_user": source_user,
                        "discovered_at": source_record.get("discovered_at") or datetime.now().isoformat(),
                        "kif_status": STATUS_INDEXED,
                        "has_kif_file": True,
                        "is_indexed": True,
                        "indexed_at": datetime.now().isoformat(),
                        "last_error": None
                    }, Game.game_id == game_id)
                    print(f"  [+] Indexed in NoSQL database.")
            except Exception as ex:
                crawl_records.update({
                    "kif_status": STATUS_KIF_DOWNLOADED if os.path.exists(file_path) else STATUS_KIF_MISSING,
                    "has_kif_file": os.path.exists(file_path),
                    "is_indexed": False,
                    "last_attempt_at": datetime.now().isoformat(),
                    "last_error": f"Indexing failed: {ex}"
                }, Game.game_id == game_id)
                print(f"  [!] Failed to index in NoSQL: {ex}")

            # Polite delay to prevent server-side rate limiting
            await asyncio.sleep(1.5)

if __name__ == "__main__":
    asyncio.run(main())
