import os
import asyncio
import json
import sys
import re
from typing import Optional
import httpx
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
            
            # Parse the extracted JSON string
            data = json.loads(match.group(1).strip())
            
            # Navigate to the kif text within the nested JSON structure
            return data.get("shogi_wars", {}).get("kif")

        except Exception as e:
            print(f"  [!] Error during download of {game_id}: {e}")
            return None

async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 kif_downloader.py [input_jsonl_file]")
        return

    input_file = sys.argv[1]
    save_dir = "kif_data"
    
    # Create output directory if it doesn't exist
    os.makedirs(save_dir, exist_ok=True)

    downloader = SwarsKifDownloader()
    
    async with httpx.AsyncClient(headers=downloader.headers, timeout=20.0) as client:
        with open(input_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                
                record = json.loads(line)
                game_id = record["game_id"]
                file_path = os.path.join(save_dir, f"{game_id}.kif")

                # Check if the file was already downloaded to avoid redundant requests
                if os.path.exists(file_path):
                    continue

                print(f"[*] Fetching KIF for: {game_id}")
                kif_text = await downloader.fetch_kif(client, game_id)
                
                if kif_text:
                    with open(file_path, "w", encoding="utf-8") as out:
                        out.write(kif_text)
                    print(f"  [+] Saved: {file_path}")
                
                # Polite delay to prevent server-side rate limiting
                await asyncio.sleep(1.5)

if __name__ == "__main__":
    asyncio.run(main())
