import os
import asyncio
import re
import sys
import json
from datetime import datetime
from typing import List, Set
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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

    async def fetch_game_ids(self, client: httpx.AsyncClient, user_id: str) -> List[str]:
        """Fetch Game IDs for a user with specific game_type."""
        url = "https://shogiwars.heroz.jp/games/history"
        # gtype is now dynamic (sb, s1, etc.)
        params = {"gtype": self.game_type, "user_id": user_id, "locale": "ja"}
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return self.game_id_pattern.findall(response.text)
        except Exception as e:
            print(f"  [!] History fetch error for {user_id}: {e}")
            return []

def deduplicate_file(filename: str):
    """Final deduplication of the JSONL file."""
    if not os.path.exists(filename): return
    unique_records = {}
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            unique_records[record["game_id"]] = record
    with open(filename, "w", encoding="utf-8") as f:
        for record in unique_records.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[*] Final unique count: {len(unique_records)}")

async def main():
    if len(sys.argv) < 3:
        print("Usage: python swars_crawler.py [start] [end] [game_type (optional)]")
        print("Example: python swars_crawler.py 1 100 s1")
        return

    start_rank = int(sys.argv[1])
    end_rank = int(sys.argv[2])
    # Default to 'sb' (3-min) if not specified
    game_type = sys.argv[3] if len(sys.argv) > 3 else "sb"
    
    crawler = SwarsCrawler(game_type=game_type)
    output_file = f"games_{game_type}.jsonl"
    
    seen_in_session: Set[str] = set()

    async with httpx.AsyncClient(headers=crawler.headers, timeout=20.0) as client:
        for offset in range(start_rank, end_rank + 1, 25):
            print(f"[*] Ranking Offset: {offset} (Type: {game_type})")
            user_ids = await crawler.fetch_user_ids(client, offset)
            
            for uid in user_ids:
                game_ids = await crawler.fetch_game_ids(client, uid)
                new_count = 0
                with open(output_file, "a", encoding="utf-8") as f:
                    for gid in game_ids:
                        if gid not in seen_in_session:
                            f.write(json.dumps({
                                "game_id": gid,
                                "type": game_type,
                                "user": uid,
                                "ts": datetime.now().isoformat()
                            }) + "\n")
                            seen_in_session.add(gid)
                            new_count += 1
                print(f"  [-] {uid}: +{new_count}")
                await asyncio.sleep(1.0)

    deduplicate_file(output_file)

if __name__ == "__main__":
    asyncio.run(main())
