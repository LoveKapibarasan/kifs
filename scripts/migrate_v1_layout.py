#!/usr/bin/env python3
"""Move a v1 checkout's data into the v2 layout, without losing anything.

v1 kept everything in the repository root:

    kif_data/  kifu_db.json  crawler_state.json  user_ranks.json

v2 keeps it all under ``$KIFS_DATA_DIR`` (default ``./data``):

    data/kif/  data/kifu_db.json  data/state/frontier.json  data/state/user_ranks.json

The crawl records are carried across as-is and gain the new retry fields; the
``crawled_users``/``user_queue`` state becomes a frontier where every known user
is scheduled for a revisit rather than being retired forever (issue #3).

Run with ``--dry-run`` first to see what would move.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import orjson  # noqa: E402

from kifs.config import load_settings  # noqa: E402
from kifs.storage.database import isoformat, utcnow  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]



#: Downstream artifacts produced from the KIFs (KIF -> CSA -> HCPE for
#: python-dlshogi2). Not collected by this pipeline, but they were sitting in
#: the repository root and belong with the rest of the dataset.
DERIVED_DIRS = {
    "kif_data/csa": "derived/csa",
    "converted": "derived/converted",
    "human_data": "derived/human_data",
}


def migrate_derived(source: Path, data_dir: Path, dry_run: bool, move: bool) -> None:
    for relative_old, relative_new in DERIVED_DIRS.items():
        old = source / relative_old
        if not old.is_dir():
            continue
        new = data_dir / relative_new
        count = sum(1 for _ in old.rglob("*") if _.is_file())
        print(f"[*] {relative_old}: {count} files -> {new}")
        if dry_run or new.exists():
            if new.exists():
                print(f"    (skipped, {new} already exists)")
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(old), new)
        else:
            shutil.copytree(old, new)


def migrate_kif_dir(old: Path, new: Path, dry_run: bool, move: bool) -> int:
    if not old.is_dir():
        print(f"[-] {old} does not exist; nothing to move.")
        return 0
    files = list(old.glob("*.kif"))
    print(f"[*] {len(files)} KIF files in {old} -> {new}")
    if dry_run:
        return len(files)
    new.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in files:
        target = new / path.name
        if target.exists():
            continue
        if move:
            shutil.move(str(path), target)
        else:
            shutil.copy2(path, target)
        moved += 1
    print(f"[+] {'moved' if move else 'copied'} {moved} files")
    return moved


def migrate_db(old: Path, new: Path, dry_run: bool) -> None:
    if not old.is_file():
        print(f"[-] {old} does not exist; nothing to move.")
        return
    raw = orjson.loads(old.read_bytes())
    games = raw.get("_default", {})
    records = raw.get("crawl_records", {})
    print(f"[*] {old.name}: {len(games)} games, {len(records)} crawl records -> {new}")

    # Give every carried-over record the fields the retry scheduler needs.
    for record in records.values():
        record.setdefault("attempts", 0)
        record.setdefault("next_retry_at", None)
        record.setdefault("last_attempt_at", None)
        record.setdefault("last_error", None)

    if dry_run:
        return
    new.parent.mkdir(parents=True, exist_ok=True)
    tmp = new.with_suffix(new.suffix + ".tmp")
    tmp.write_bytes(orjson.dumps(raw))
    tmp.replace(new)
    print(f"[+] wrote {new} ({new.stat().st_size / 1e6:.1f} MB, indent stripped)")


def migrate_state(old: Path, new: Path, dry_run: bool, recrawl_hours: float) -> None:
    if not old.is_file():
        print(f"[-] {old} does not exist; starting with an empty frontier.")
        return
    state = json.loads(old.read_text(encoding="utf-8"))
    crawled = state.get("crawled_users", [])
    queued = state.get("user_queue", [])
    print(f"[*] {old.name}: {len(crawled)} crawled users, {len(queued)} queued -> {new}")

    now = utcnow()
    users: dict[str, dict] = {}
    # Previously-crawled users are spread over the recrawl window instead of all
    # coming due at once, so the first day back does not stampede the site.
    for index, user_id in enumerate(crawled):
        offset = timedelta(hours=recrawl_hours * index / max(len(crawled), 1))
        users[user_id] = {
            "first_seen_at": isoformat(now),
            "last_crawled_at": isoformat(now),
            "next_crawl_at": isoformat(now + offset),
            "games_found": 0,
            "crawls": 1,
            "migrated_from_v1": True,
        }
    pending = []
    for user_id in queued:
        if user_id in users:
            continue
        users[user_id] = {
            "first_seen_at": isoformat(now),
            "last_crawled_at": None,
            "next_crawl_at": None,
            "games_found": 0,
            "crawls": 0,
            "migrated_from_v1": True,
        }
        pending.append(user_id)

    print(f"[*] frontier: {len(users)} users, {len(pending)} never crawled")
    if dry_run:
        return
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_bytes(orjson.dumps({"users": users, "pending": pending, "seed_cursor": 0}))
    print(f"[+] wrote {new}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen.")
    parser.add_argument("--move", action="store_true",
                        help="Move KIF files instead of copying them (saves disk).")
    parser.add_argument("--source", type=Path, default=ROOT,
                        help="v1 checkout to migrate from (default: this repository).")
    args = parser.parse_args()

    settings = load_settings()
    source = args.source.resolve()
    print(f"[*] source: {source}")
    print(f"[*] target: {settings.data_dir}")

    migrate_kif_dir(source / "kif_data", settings.kif_dir, args.dry_run, args.move)
    migrate_db(source / "kifu_db.json", settings.db_path, args.dry_run)
    migrate_state(source / "crawler_state.json", settings.frontier_path,
                  args.dry_run, settings.user_recrawl_hours)

    migrate_derived(source, settings.data_dir, args.dry_run, args.move)

    old_ranks = source / "user_ranks.json"
    if old_ranks.is_file():
        target = settings.state_dir / "user_ranks.json"
        print(f"[*] {old_ranks.name} -> {target}")
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_ranks, target)

    if args.dry_run:
        print("\n[dry-run] nothing was written.")
    else:
        print("\n[+] migration complete. Verify with: kifs status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
