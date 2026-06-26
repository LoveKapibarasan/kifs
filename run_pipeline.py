#!/usr/bin/env python3
import os
import sys
import json
import time
import signal
import asyncio
import logging
from datetime import datetime
from typing import Set, List
import httpx
from dotenv import load_dotenv

# Import NoSQL and crawler/downloader modules
from index_to_nosql import OrJSONStorage, parse_kif
from swars_crawler import SwarsCrawler
from kif_downloader import SwarsKifDownloader
from tinydb import TinyDB, Query

# Setup logging to both console and log file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

STATE_FILE = "crawler_state.json"
DB_FILE = "kifu_db.json"

class PipelineManager:
    def __init__(self):
        load_dotenv()
        self.db = TinyDB(DB_FILE, storage=OrJSONStorage)
        self.downloader = SwarsKifDownloader()
        
        # Default to 'sb' (3-min) but we can support others too
        self.crawler = SwarsCrawler(game_type="sb")
        
        self.crawled_users: Set[str] = set()
        self.user_queue: List[str] = []
        self.indexed_game_ids: Set[str] = set()
        self.running = True

    def load_state(self):
        # 1. Load indexed game IDs from TinyDB
        self.indexed_game_ids = {doc["game_id"] for doc in self.db.all() if "game_id" in doc}
        logging.info(f"Loaded {len(self.indexed_game_ids)} indexed game IDs from database.")

        # 2. Load crawler state if it exists
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    self.crawled_users = set(state.get("crawled_users", []))
                    self.user_queue = state.get("user_queue", [])
                logging.info(f"Loaded state from {STATE_FILE}: {len(self.crawled_users)} crawled users, {len(self.user_queue)} in queue.")
            except Exception as e:
                logging.error(f"Failed to load state file {STATE_FILE}: {e}. Starting fresh.")

        # 3. Seed users if queue is empty
        if not self.user_queue:
            logging.info("Queue is empty. Will seed users from event rankings on startup.")

    def save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "crawled_users": list(self.crawled_users),
                    "user_queue": self.user_queue
                }, f, ensure_ascii=False, indent=2)
            logging.info(f"Saved crawler state to {STATE_FILE}.")
        except Exception as e:
            logging.error(f"Failed to save state to {STATE_FILE}: {e}")

    async def seed_from_rankings(self, client: httpx.AsyncClient):
        # Fetch some active users from event ranking to seed the queue
        logging.info("Fetching ranking offsets to seed users...")
        seeds = set()
        # Fetch top offsets
        for offset in [1, 26, 51, 76, 101, 201, 301, 401, 501]:
            user_ids = await self.crawler.fetch_user_ids(client, offset)
            for uid in user_ids:
                if uid not in self.crawled_users:
                    seeds.add(uid)
            await asyncio.sleep(1.5) # Polite delay
        
        self.user_queue = list(seeds)
        logging.info(f"Seeded queue with {len(self.user_queue)} users from event rankings.")
        self.save_state()

    def extract_players(self, game_id: str):
        # Game ID format: "sente-gote-timestamp"
        parts = game_id.rsplit('-', 1)
        if len(parts) != 2:
            return None, None
        players_part = parts[0]
        player_parts = players_part.split('-', 1)
        if len(player_parts) != 2:
            return None, None
        return player_parts[0], player_parts[1]

    async def process_pipeline(self):
        logging.info("Starting pipeline process...")
        
        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.shutdown)
            except NotImplementedError:
                pass

        async with httpx.AsyncClient(headers=self.downloader.headers, timeout=20.0) as client:
            if not self.user_queue:
                await self.seed_from_rankings(client)

            user_process_count = 0

            while self.running and self.user_queue:
                user_id = self.user_queue.pop(0)
                if user_id in self.crawled_users:
                    continue

                logging.info(f"Processing user: {user_id} (Queue size: {len(self.user_queue)}, Crawled: {len(self.crawled_users)})")
                self.crawled_users.add(user_id)

                # 1. Fetch game history (fetch 3 pages to discover more games)
                game_ids = []
                for p in range(1, 4):
                    if not self.running:
                        break
                    p_game_ids = await self.crawler.fetch_game_ids(client, user_id, page=p)
                    if not p_game_ids:
                        break
                    game_ids.extend(p_game_ids)
                    await asyncio.sleep(1.5) # Polite delay

                logging.info(f"  Found {len(game_ids)} games for {user_id}.")

                # 2. Process each game
                new_game_count = 0
                for game_id in game_ids:
                    if not self.running:
                        break

                    # Discover new users from the game_id (BFS graph traversal)
                    p1, p2 = self.extract_players(game_id)
                    if p1 and p1 not in self.crawled_users and p1 not in self.user_queue:
                        self.user_queue.append(p1)
                    if p2 and p2 not in self.crawled_users and p2 not in self.user_queue:
                        self.user_queue.append(p2)

                    # Download and index KIF if not already indexed
                    if game_id not in self.indexed_game_ids:
                        file_path = os.path.join("kif_data", f"{game_id}.kif")
                        
                        # Download if file doesn't exist
                        if not os.path.exists(file_path):
                            logging.info(f"    Downloading KIF for: {game_id}")
                            kif_text = await self.downloader.fetch_kif(client, game_id)
                            if kif_text:
                                os.makedirs("kif_data", exist_ok=True)
                                with open(file_path, "w", encoding="utf-8") as out:
                                    out.write(kif_text)
                                await asyncio.sleep(1.5) # Polite delay
                            else:
                                continue
                        
                        # Parse and index in TinyDB NoSQL
                        try:
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
                                    "crawler_user": user_id,
                                    "crawler_type": "sb",
                                    "crawler_ts": datetime.now().isoformat()
                                }
                                self.db.insert(doc)
                                self.indexed_game_ids.add(game_id)
                                new_game_count += 1
                        except Exception as e:
                            logging.error(f"    Failed to index {game_id}: {e}")

                logging.info(f"  Finished {user_id}: +{new_game_count} new games indexed.")
                
                # Periodically save state and prune queue to keep memory small
                user_process_count += 1
                if user_process_count % 5 == 0:
                    # Keep queue size bounded to prevent memory leakage
                    if len(self.user_queue) > 50000:
                        self.user_queue = self.user_queue[:50000]
                    self.save_state()

                # Sleep to prevent high load / rate limit
                await asyncio.sleep(1.5)

            # Save final state before exiting
            self.save_state()
            logging.info("Pipeline finished loop iteration.")

    def shutdown(self):
        logging.info("Shutdown signal received. Gracefully saving state and stopping...")
        self.running = False

async def main():
    manager = PipelineManager()
    manager.load_state()
    
    # Run in an infinite retry loop to make sure it doesn't die on temporary connection errors
    while manager.running:
        try:
            await manager.process_pipeline()
        except Exception as e:
            logging.critical(f"CRITICAL ERROR in pipeline: {e}. Retrying in 60 seconds...")
            if not manager.running:
                break
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
