#!/usr/bin/env python3
"""Backfill rank onto already-indexed games by re-downloading their KIF.

The originally-downloaded KIF files predate the new analytics API and contain no
段級. This re-fetches each game via the new API (KIF now embeds 先手段級/後手段級 =
rank at game time), overwrites the .kif on disk, and updates the DB document with
sente_rank/gote_rank (and refreshed parsed fields).

Resumable: games whose DB doc already has sente_rank set are skipped.
"""
import os
import sys
import time
import asyncio
from datetime import datetime

import httpx
from tinydb import TinyDB

from index_to_nosql import OrJSONStorage, parse_kif
from kif_downloader import SwarsKifDownloader

DB_FILE = "kifu_db.json"
KIF_DIR = "kif_data"
DELAY = 1.2
FLUSH_EVERY = 100


async def main():
    db = TinyDB(DB_FILE, storage=OrJSONStorage)
    raw = db.storage.read()
    default = raw.get("_default", {})

    # keys of games still missing rank
    todo = [k for k, d in default.items()
            if d.get("game_id") and not d.get("sente_rank") and not d.get("gote_rank")]
    print(f"[*] games total: {len(default)}, needing rank backfill: {len(todo)}")
    if not todo:
        print("[*] nothing to backfill.")
        return

    downloader = SwarsKifDownloader()
    if "sessionid=" not in downloader.headers.get("Cookie", ""):
        print("[!] ANALYTICS_SESSION (sessionid) missing in .env — cannot download. Abort.")
        return

    os.makedirs(KIF_DIR, exist_ok=True)
    start = time.time()
    done = updated = missing = 0

    def flush():
        raw["_default"] = default
        db.storage.write(raw)

    async with httpx.AsyncClient(headers=downloader.headers, timeout=20.0) as client:
        for key in todo:
            doc = default[key]
            gid = doc["game_id"]
            kif = await downloader.fetch_kif(client, gid)
            done += 1
            if kif:
                path = os.path.join(KIF_DIR, f"{gid}.kif")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(kif)
                pd = parse_kif(path)
                if pd:
                    doc.update({
                        "sente": pd["sente"], "gote": pd["gote"],
                        "sente_rank": pd["sente_rank"], "gote_rank": pd["gote_rank"],
                        "start_time": pd["start_time"], "end_time": pd["end_time"],
                        "location": pd["location"], "handicap": pd["handicap"],
                        "result": pd["result"], "moves": pd["moves"],
                        "total_moves": pd["total_moves"], "raw_headers": pd["raw_headers"],
                        "rank_backfilled_at": datetime.now().isoformat(),
                    })
                    if pd["sente_rank"] or pd["gote_rank"]:
                        updated += 1
            else:
                missing += 1

            if done % FLUSH_EVERY == 0:
                flush()
                rate = done / (time.time() - start)
                eta = (len(todo) - done) / rate / 60 if rate else 0
                print(f"  [{done}/{len(todo)}] updated={updated} missing={missing} "
                      f"(~{eta:.0f} min left) last={gid}")
            await asyncio.sleep(DELAY)

    flush()
    print(f"[+] backfill done: {done} processed, {updated} now have rank, {missing} unavailable.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
