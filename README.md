# Shogi Wars Game Data Pipeline

A high-performance asynchronous pipeline to crawl user rankings from Shogi Wars, extract game IDs, and download raw KIF files from Kishin Analytics.

## Features

- **Ranking Crawler**: Scrapes user IDs from event rankings with customizable offsets.
- **Game History Extractor**: Supports multiple game types (`sb` for 3-min, `s1` for 10-sec, etc.).
- **Deduplication**: Automatically removes duplicate Game IDs both in-memory and via post-processing.
- **KIF Downloader**: Fetches and decodes raw KIF data into readable `.kif` files.
- **Resume Support**: Skips already downloaded files to save time and bandwidth.

## Setup

    * Create a .env file in the root directory.

    * Add your Shogi Wars web session cookie:


```txt
WEB_SESSION=your_session_cookie_here
```

## Usage
1. Crawl Game IDs

Run the crawler to scan rankings from start_rank to end_rank.

```bash
# Usage: python swars_crawler.py [start] [end] [game_type]
python3 swars_crawler.py 1 100 sb
```

    * This generates `games_sb.jsonl` (JSON Lines format).

    * Each line contains a unique game_id, source_user, and timestamp.

2. Download KIF Files

Use the generated JSONL file to download actual move sequences.

```bash
# Usage: python3 kif_downloader.py [input_jsonl]
python3 kif_downloader.py games_sb.jsonl
```

    * KIF files are saved into the `kif_data/` directory.

    * The script includes a 1.5s delay between requests to respect server limits.
