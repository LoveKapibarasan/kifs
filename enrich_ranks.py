#!/usr/bin/env python3
"""Enrich indexed games with player ranks scraped from Shogi Wars mypage.

Two phases:
  1. fetch    - for every distinct player in the DB, fetch their current 3-min
                rank and cache it in user_ranks.json (resumable, polite delay).
  2. annotate - write sente_rank / sente_rating / gote_rank / gote_rating onto
                every game document in kifu_db.json (single bulk write).

Run `python enrich_ranks.py` for both phases, or `--annotate-only`.
Ranks are the players' *current* rank, not their rank at game time.
"""
import os
import sys
import json
import time
import asyncio
import argparse
from datetime import datetime

import httpx
from dotenv import load_dotenv
from tinydb import TinyDB, Query

from index_to_nosql import OrJSONStorage
from rank_fetcher import fetch_user_rank

DB_FILE = "kifu_db.json"
RANKS_FILE = "user_ranks.json"
DELAY = 1.5
FLUSH_EVERY = 25


def load_ranks() -> dict:
    if os.path.exists(RANKS_FILE):
        with open(RANKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ranks(ranks: dict):
    tmp = RANKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ranks, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RANKS_FILE)


def distinct_players(db) -> list:
    users = set()
    for g in db.all():
        if "game_id" not in g:
            continue
        if g.get("sente"):
            users.add(g["sente"])
        if g.get("gote"):
            users.add(g["gote"])
    return sorted(users)


async def phase_fetch():
    load_dotenv()
    ws = os.getenv("WEB_SESSION")
    headers = {"User-Agent": "Mozilla/5.0"}
    if ws:
        headers["Cookie"] = f"_web_session={ws}"

    db = TinyDB(DB_FILE, storage=OrJSONStorage)
    players = distinct_players(db)
    ranks = load_ranks()
    todo = [u for u in players if u not in ranks]
    print(f"[*] distinct players: {len(players)}, already cached: {len(ranks)}, to fetch: {len(todo)}")
    if not todo:
        print("[*] nothing to fetch.")
        return

    start = time.time()
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as c:
        for i, user_id in enumerate(todo, 1):
            info = await fetch_user_rank(c, user_id)
            ranks[user_id] = info if info else {"user_id": user_id, "rank_3m": None,
                                                "rating_3m": None, "highest_3m": None,
                                                "modes": {}, "error": True}
            ranks[user_id]["fetched_at"] = datetime.now().isoformat()
            if i % FLUSH_EVERY == 0:
                save_ranks(ranks)
                rate = i / (time.time() - start)
                eta = (len(todo) - i) / rate / 60 if rate else 0
                print(f"  [{i}/{len(todo)}] {user_id} -> {ranks[user_id].get('rank_3m')} "
                      f"(~{eta:.0f} min left)")
            await asyncio.sleep(DELAY)
    save_ranks(ranks)
    print(f"[+] fetch complete: {len(ranks)} users cached in {RANKS_FILE}")


def phase_annotate():
    ranks = load_ranks()
    if not ranks:
        print("[!] no user_ranks.json yet; run the fetch phase first.")
        return
    db = TinyDB(DB_FILE, storage=OrJSONStorage)
    table = db.table("_default")
    docs = table.all()

    def rank_of(name):
        r = ranks.get(name) or {}
        return r.get("rank_3m"), r.get("rating_3m")

    updated = 0
    with_rank = 0
    new_docs = {}
    for doc in docs:
        gid = doc.get("game_id")
        if not gid:
            continue
        sr, srt = rank_of(doc.get("sente"))
        gr, grt = rank_of(doc.get("gote"))
        doc["sente_rank"] = sr
        doc["sente_rating"] = srt
        doc["gote_rank"] = gr
        doc["gote_rating"] = grt
        new_docs[doc.doc_id] = doc
        updated += 1
        if sr or gr:
            with_rank += 1

    # single bulk write via the storage layer (atomic)
    raw = db.storage.read()
    raw["_default"] = {str(k): v for k, v in new_docs.items()}
    db.storage.write(raw)
    print(f"[+] annotated {updated} games; {with_rank} have at least one known rank.")


def main():
    ap = argparse.ArgumentParser(description="Enrich games with player ranks.")
    ap.add_argument("--annotate-only", action="store_true", help="Skip fetching; only write ranks onto games.")
    ap.add_argument("--fetch-only", action="store_true", help="Only fetch ranks into user_ranks.json.")
    args = ap.parse_args()

    if args.annotate_only:
        phase_annotate()
        return
    asyncio.run(phase_fetch())
    if not args.fetch_only:
        phase_annotate()


if __name__ == "__main__":
    main()
