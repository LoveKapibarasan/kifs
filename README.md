# Shogi Wars Game Data Pipeline

A high-performance asynchronous pipeline to crawl user rankings from Shogi Wars, extract game IDs, and download raw KIF files from Kishin Analytics.

## Features

- **Ranking Crawler**: Scrapes user IDs from event rankings with customizable offsets and supports pagination crawling.
- **Game History Extractor**: Supports multiple game types (`sb` for 3-min, `s1` for 10-sec, etc.).
- **Deduplication**: Automatically removes duplicate Game IDs both in-memory and via post-processing.
- **KIF Downloader**: Fetches and decodes raw KIF data into readable `.kif` files.
- **NoSQL Indexing & Fast Search**: Uses TinyDB with a high-performance Rust-based `orjson` parser to index KIF files and enable sub-second lookups by Game ID or player name.
- **Resume Support**: Skips already downloaded files to save time and bandwidth.

## Setup

1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the root directory:
   ```txt
   WEB_SESSION=your_session_cookie_here
   ```

## Usage

*Note: All commands below assume you have activated the `.venv` virtual environment. If not, prefix python with `.venv/bin/python3`.*

### 1. Crawl Game IDs (with pagination)

Run the crawler to scan rankings from start_rank to end_rank. You can crawl multiple pages of history for each user using the `--pages` flag.

```bash
# Usage: python swars_crawler.py [start] [end] [game_type] [--pages N]
python3 swars_crawler.py 1 100 sb --pages 2
```

- This generates `games_sb.jsonl` (JSON Lines format).
- Each line contains a unique game_id, source_user, and timestamp.

### 2. Download KIF Files

Use the generated JSONL file to download actual move sequences.

```bash
# Usage: python3 kif_downloader.py [input_jsonl]
python3 kif_downloader.py games_sb.jsonl
```

- KIF files are saved into the `kif_data/` directory.
- The downloader automatically indexes newly downloaded games into `kifu_db.json` in real-time.

### 3. Index into NoSQL Database & Search

We use **TinyDB** backed by **orjson** to store and query games very fast.

* **Index games:**
  ```bash
  python3 index_to_nosql.py --index
  ```
  This parses KIF files under `kif_data/` and metadata from `games_*.jsonl`, then indexes them in `kifu_db.json`.

* **Search indexed games:**
  ```bash
  # Fast O(1) Search by Game ID
  python3 index_to_nosql.py --search --game-id "00112233-ernes-20260419_102745"

  # Search by player name (either Sente or Gote)
  python3 index_to_nosql.py --search --player "playerName"

  # Search with filters and limit results
  python3 index_to_nosql.py --search --sente "playerA" --gote "playerB" --limit 5

  # Export search results to a JSON file
  python3 index_to_nosql.py --search --player "playerName" --export results.json
  ```


