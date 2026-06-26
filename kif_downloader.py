import os
import asyncio
import re
from typing import Optional
import httpx
import orjson
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

class SwarsKifDownloader:
    def __init__(self):
        self.web_session = os.getenv("WEB_SESSION")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        # Include session cookie if present in .env
        if self.web_session:
            self.headers["Cookie"] = f"_web_session={self.web_session}"
        
        # Regex to extract the JSON content from the <script id="passed-data"> tag
        self.data_pattern = re.compile(r'<script id="passed-data"[^>]*>(.*?)</script>', re.DOTALL)

    async def fetch_kif(self, client: httpx.AsyncClient, game_id: str) -> Optional[str]:
        """
        Fetches the specific game page and extracts the KIF text content.
        """
        url = f"https://kishin-analytics.heroz.jp/?wars_game_id={game_id}"
        try:
            response = await client.get(url)
            response.raise_for_status()
            
            match = self.data_pattern.search(response.text)
            if not match:
                return None
            
            # Parse the data embedded in the analytics page.
            data = orjson.loads(match.group(1).strip())
            
            # Navigate to the kif text within the nested JSON structure
            return data.get("shogi_wars", {}).get("kif")

        except Exception as e:
            print(f"  [!] Error during download of {game_id}: {e}")
            return None

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download KIF files by game ID and index them into TinyDB.")
    parser.add_argument("game_ids", nargs="+", help="Shogi Wars game IDs to download.")
    parser.add_argument("--db", default="kifu_db.json", help="TinyDB database path.")
    parser.add_argument("--kif-dir", default="kif_data", help="Directory to save KIF files.")
    parser.add_argument("--game-type", default=None, help="Optional game type metadata, e.g. sb or s1.")
    parser.add_argument("--source-user", default=None, help="Optional crawler/source user metadata.")
    args = parser.parse_args()

    save_dir = args.kif_dir
    
    # Create output directory if it doesn't exist
    os.makedirs(save_dir, exist_ok=True)

    downloader = SwarsKifDownloader()
    
    # Initialize TinyDB
    from tinydb import TinyDB, Query
    from index_to_nosql import OrJSONStorage
    db = TinyDB(args.db, storage=OrJSONStorage)
    Game = Query()
    
    async with httpx.AsyncClient(headers=downloader.headers, timeout=20.0) as client:
        for game_id in args.game_ids:
            file_path = os.path.join(save_dir, f"{game_id}.kif")

            if os.path.exists(file_path):
                print(f"[*] Already exists: {file_path}")
            else:
                print(f"[*] Fetching KIF for: {game_id}")
                kif_text = await downloader.fetch_kif(client, game_id)
                if not kif_text:
                    await asyncio.sleep(1.5)
                    continue

                with open(file_path, "w", encoding="utf-8") as out:
                    out.write(kif_text)
                print(f"  [+] Saved: {file_path}")

            try:
                from index_to_nosql import parse_kif
                parsed_data = parse_kif(file_path)
                if parsed_data:
                    doc = {
                        "game_id": game_id,
                        "sente": parsed_data["sente"],
                        "gote": parsed_data["gote"],
                        "start_time": parsed_data["start_time"],
                        "end_time": parsed_data["end_time"],
                        "location": parsed_data["location"],
                        "handicap": parsed_data["handicap"],
                        "result": parsed_data["result"],
                        "moves": parsed_data["moves"],
                        "total_moves": parsed_data["total_moves"],
                        "raw_headers": parsed_data["raw_headers"],
                        "crawler_user": args.source_user,
                        "crawler_type": args.game_type,
                        "crawler_ts": None
                    }
                    db.upsert(doc, Game.game_id == game_id)
                    print(f"  [+] Indexed in NoSQL database.")
            except Exception as ex:
                print(f"  [!] Failed to index in NoSQL: {ex}")

            # Polite delay to prevent server-side rate limiting
            await asyncio.sleep(1.5)

if __name__ == "__main__":
    asyncio.run(main())
