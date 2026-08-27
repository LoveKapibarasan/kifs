import os
import asyncio
import re
import sys
from datetime import datetime
from typing import List
import httpx
from dotenv import load_dotenv
from tinydb import TinyDB, Query
from index_to_nosql import OrJSONStorage, STATUS_KIF_MISSING

# Load environment variables
load_dotenv()

DB_FILE = "kifu_db.json"
CRAWL_RECORDS_TABLE = "crawl_records"

class SwarsCrawler:
    def __init__(self, game_type: str = "sb"):
        web_session = os.getenv("WEB_SESSION")
        if not web_session:
            print("Error: WEB_SESSION not found in .env")
            sys.exit(1)

        self.game_type = game_type
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": f"_web_session={web_session}"
        }
        
        # Pattern to capture User IDs from ranking links
        self.user_id_pattern = re.compile(r'/users/mypage/([^?"]+)')
        
        # Dynamic pattern for Game IDs based on the analytics link
        # This covers: wars_game_id=USER1-USER2-YYYYMMDD_HHMMSS
        self.game_id_pattern = re.compile(r'wars_game_id=([^&"]+)')

    async def fetch_user_ids(self, client: httpx.AsyncClient, offset: int) -> List[str]:
        """Fetch User IDs from the ranking page."""
        url = "https://shogiwars.heroz.jp/events/point2026"
        params = {"locale": "ja", "rank_criteria": "point", "start": offset}
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return list(dict.fromkeys(self.user_id_pattern.findall(response.text)))
        except Exception as e:
            print(f"  [!] Ranking fetch error at {offset}: {e}")
            return []

    async def fetch_game_ids(self, client: httpx.AsyncClient, user_id: str, page: int = 1) -> List[str]:
        """Fetch Game IDs for a user with specific game_type and page number."""
        url = "https://shogiwars.heroz.jp/games/history"
        # gtype is now dynamic (sb, s1, etc.)
        params = {
            "gtype": self.game_type,
            "user_id": user_id,
            "locale": "ja",
            "page": page,
            "init_pos_type": "normal"
        }
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return self.game_id_pattern.findall(response.text)
        except Exception as e:
            print(f"  [!] History fetch error for {user_id} (page {page}): {e}")
            return []

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Crawl Shogi Wars game IDs into the NoSQL database.")
    parser.add_argument("start", type=int, help="Start ranking offset (e.g., 1)")
    parser.add_argument("end", type=int, help="End ranking offset (e.g., 100)")
    parser.add_argument("game_type", nargs="?", default="sb", help="Game type: sb (3-min), s1 (10-sec), etc.")
    parser.add_argument("--pages", type=int, default=1, help="Number of pages to crawl per user (default: 1)")
    parser.add_argument("--db", default=DB_FILE, help=f"TinyDB database path (default: {DB_FILE})")

    args = parser.parse_args()
    start_rank = args.start
    end_rank = args.end
    game_type = args.game_type
    max_pages = args.pages
    
    crawler = SwarsCrawler(game_type=game_type)
    db = TinyDB(args.db, storage=OrJSONStorage)
    crawl_records = db.table(CRAWL_RECORDS_TABLE)
    Game = Query()
    seen_game_ids = {record["game_id"] for record in crawl_records.all() if "game_id" in record}

    async with httpx.AsyncClient(headers=crawler.headers, timeout=20.0) as client:
        for offset in range(start_rank, end_rank + 1, 25):
            print(f"[*] Ranking Offset: {offset} (Type: {game_type})")
            user_ids = await crawler.fetch_user_ids(client, offset)
            
            for uid in user_ids:
                game_ids = []
                for p in range(1, max_pages + 1):
                    p_game_ids = await crawler.fetch_game_ids(client, uid, page=p)
                    if not p_game_ids:
                        break
                    game_ids.extend(p_game_ids)
                    if p < max_pages:
                        await asyncio.sleep(0.5)
                
                new_count = 0
                for gid in game_ids:
                    if gid not in seen_game_ids:
                        crawl_records.upsert({
                            "game_id": gid,
                            "game_type": game_type,
                            "source_user": uid,
                            "discovered_at": datetime.now().isoformat(),
                            "kif_status": STATUS_KIF_MISSING,
                            "has_kif_file": False,
                            "is_indexed": False
                        }, Game.game_id == gid)
                        seen_game_ids.add(gid)
                        new_count += 1
                print(f"  [-] {uid}: +{new_count} (across {max_pages} pages)")
                await asyncio.sleep(1.0)

    print(f"[*] Total crawl records in NoSQL: {len(crawl_records)}")

if __name__ == "__main__":
    asyncio.run(main())
