"""Single entry point for every operation (issue #1).

    kifs serve          run the collector continuously (what systemd starts)
    kifs crawl          one-shot: seed from the ranking and crawl N users
    kifs download       fetch KIFs for specific games, or the whole backlog
    kifs reconcile      re-sync the database with the KIF files on disk
    kifs search         query indexed games
    kifs stats          dataset and crawl-status summary
    kifs status         operational snapshot (what the service has left to do)
    kifs report         build the daily report; --send mails it
    kifs export         write kifu_db.json (TinyDB format) from the database
    kifs migrate-sqlite import a v2 kifu_db.json + frontier.json into SQLite
    kifs ranks fetch|annotate|backfill
    kifs secrets check|push
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from kifs.config import Settings, load_settings, resolve_credentials
from kifs.logging_setup import setup_logging
from kifs.storage.database import (
    STATUS_INDEXED,
    STATUS_KIF_MISSING,
    STATUS_KIF_UNAVAILABLE,
    KifuDatabase,
)

log = logging.getLogger("kifs")


def _open_db(settings: Settings, read_only: bool = False) -> KifuDatabase:
    """Open the database. Read-only commands must say so: they run against a
    live collector, and opening for write would block on its lock."""
    settings.ensure_dirs()
    return KifuDatabase(
        settings.db_path,
        flush_every_writes=settings.flush_every_writes,
        flush_every_seconds=settings.flush_every_seconds,
        read_only=read_only,
    ).open()


# -- commands ---------------------------------------------------------
def cmd_serve(args, settings: Settings) -> int:
    from kifs.pipeline.service import CollectorService
    from kifs.storage.lock import LockHeld

    resolve_credentials(settings, use_infisical=not args.no_infisical)
    if not settings.web_session:
        log.error("WEB_SESSION is not available from Infisical or the environment.")
        return 2
    log.info("Credentials resolved from: %s", settings.credential_source)
    log.info("Data directory: %s", settings.data_dir)

    service = CollectorService(settings, retry_batch=args.retry_batch,
                               max_cycles=args.max_cycles)
    try:
        asyncio.run(service.run())
    except LockHeld as exc:
        log.error("%s", exc)
        return 3
    except KeyboardInterrupt:
        log.info("Interrupted.")
    return 0


def cmd_crawl(args, settings: Settings) -> int:
    """One-shot crawl: useful for a manual top-up without running the service."""
    from kifs.pipeline.service import CollectorService

    resolve_credentials(settings, use_infisical=not args.no_infisical)
    if not settings.web_session:
        log.error("WEB_SESSION is not available.")
        return 2
    service = CollectorService(settings, retry_batch=args.retry_batch,
                               max_cycles=args.users)
    asyncio.run(service.run())
    return 0


def cmd_download(args, settings: Settings) -> int:
    import httpx

    from kifs.clients.kishin import KishinAnalyticsClient
    from kifs.pipeline.downloader import Downloader

    resolve_credentials(settings, use_infisical=not args.no_infisical)
    db = _open_db(settings)

    if args.game_ids:
        targets = [(game_id, None, None) for game_id in args.game_ids]
    else:
        due = db.due_records(limit=args.limit)
        targets = [(r.game_id, r.get("source_user"), r.get("game_type")) for r in due]
    log.info("Downloading %d game(s).", len(targets))

    async def run() -> None:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64)"}
        async with httpx.AsyncClient(headers=headers, timeout=settings.request_timeout) as client:
            kishin = KishinAnalyticsClient(client, settings.analytics_session,
                                           settings.web_session)
            downloader = Downloader(kishin, db, settings)
            for game_id, source_user, game_type in targets:
                outcome = await downloader.collect(game_id, source_user, game_type)
                print(f"{outcome:9s} {game_id}")
                await asyncio.sleep(settings.request_delay)

    try:
        asyncio.run(run())
    finally:
        db.close()
    return 0


def cmd_reconcile(args, settings: Settings) -> int:
    from kifs.pipeline.indexer import reconcile

    db = _open_db(settings)
    try:
        counts = reconcile(db, settings)
        print(json.dumps(counts, indent=2))
    finally:
        db.close()
    return 0


def cmd_search(args, settings: Settings) -> int:
    from kifs.query.search import search_games

    db = _open_db(settings, read_only=True)
    hits = search_games(
        db, game_id=args.game_id, player=args.player, sente=args.sente,
        gote=args.gote, result=args.result, min_moves=args.min_moves, limit=args.limit,
    )
    print(f"\n{len(hits)} matching game(s):")
    for index, game in enumerate(hits, start=1):
        print(f"\n[{index}] {game.get('game_id')}")
        print(f"    先手 {game.get('sente')} ({game.get('sente_rank')})"
              f"  vs  後手 {game.get('gote')} ({game.get('gote_rank')})")
        print(f"    開始 {game.get('start_time')} | 結果 {game.get('result')}"
              f" | {game.get('total_moves')}手")
    db.close()
    return 0


def cmd_stats(args, settings: Settings) -> int:
    from kifs.query.search import summarize

    db = _open_db(settings, read_only=True)
    report = summarize(db)
    print("\n=== Database ===")
    print(f"Games indexed      : {report['total_games']}")
    print(f"Unique players     : {report['unique_players']}")
    print(f"Average moves      : {report['average_moves']:.1f}")
    print(f"Games with a rank  : {report['games_with_rank']}")
    print("\n--- Results ---")
    for outcome, count in report["results"].items():
        share = count / report["total_games"] * 100 if report["total_games"] else 0
        print(f"  {outcome}: {count} ({share:.1f}%)")
    print("\n--- Most active players ---")
    for name, count in report["top_players"]:
        print(f"  {name}: {count}")
    print("\n--- Crawl status ---")
    for status, count in sorted(report["crawl_status"].items()):
        print(f"  {status}: {count}")
    db.close()
    return 0


def cmd_status(args, settings: Settings) -> int:
    """What the collector still has to do — the operational view (issue #2)."""
    from kifs.storage.frontier import Frontier

    db = _open_db(settings, read_only=True)
    frontier = Frontier(db, recrawl_hours=settings.user_recrawl_hours)
    counts = db.status_counts()
    due = len(db.due_records())
    kif_files = sum(1 for _ in settings.kif_dir.glob("*.kif"))

    report = {
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "db_size_mb": round(db.size_bytes() / 1e6, 1),
        "kif_files_on_disk": kif_files,
        "games_indexed": db.count_games(),
        "crawl_records": db.count_records(),
        "records_by_status": counts,
        "records_due_now": due,
        "records_given_up": counts.get(STATUS_KIF_UNAVAILABLE, 0),
        **frontier.stats(),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    db.close()
    return 0


def cmd_export(args, settings: Settings) -> int:
    from kifs.storage.export import export_tinydb_json

    db = _open_db(settings, read_only=True)
    try:
        target = args.output or (settings.data_dir / "kifu_db.json")
        counts = export_tinydb_json(db, Path(target), include_records=not args.games_only,
                                    indent=args.indent)
        print(json.dumps(counts, indent=2))
    finally:
        db.close()
    return 0


def cmd_migrate_sqlite(args, settings: Settings) -> int:
    from kifs.storage.migrate import migrate_json_to_sqlite

    counts = migrate_json_to_sqlite(
        settings,
        json_path=args.json or settings.legacy_db_path,
        frontier_path=args.frontier or settings.legacy_frontier_path,
        dry_run=args.dry_run,
    )
    print(json.dumps(counts, indent=2))
    return 0


def cmd_report(args, settings: Settings) -> int:
    """Build the daily report and, with --send, mail it (the systemd timer's job)."""
    from kifs.notify.mailer import Mailer, MailError
    from kifs.notify.report import build_report, render_html, render_text

    resolve_credentials(settings, use_infisical=not args.no_infisical)
    # --dry-run must not move the baseline, or the next real report would
    # under-report the delta.
    report = build_report(settings, persist=args.send)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    if not args.send:
        return 0

    mailer = Mailer(settings)
    if not mailer.configured:
        log.error("SMTP is not configured; expected SMTP_* and REPORT_TO from Infisical.")
        return 2

    total = report["current"]["games_indexed"]
    added = report["delta"].get("games_indexed")
    subject = f"[kifs] 日次レポート {total:,} 対局"
    if added is not None:
        subject += f" (+{added:,})"
    if report["warnings"]:
        subject = f"[kifs] ⚠ 日次レポート {total:,} 対局"

    try:
        mailer.send(subject, render_text(report), render_html(report), to=args.to)
    except MailError as exc:
        log.error("Could not send the report: %s", exc)
        return 1
    return 0


def cmd_ranks(args, settings: Settings) -> int:
    from kifs.ranks.backfill import backfill_ranks
    from kifs.ranks.enrich import annotate_games, fetch_ranks

    resolve_credentials(settings, use_infisical=not args.no_infisical)
    db = _open_db(settings)
    try:
        if args.rank_action == "fetch":
            asyncio.run(fetch_ranks(db, settings))
        elif args.rank_action == "annotate":
            annotate_games(db, settings)
        elif args.rank_action == "backfill":
            asyncio.run(backfill_ranks(db, settings, limit=args.limit))
    finally:
        db.close()
    return 0


def cmd_secrets(args, settings: Settings) -> int:
    from kifs.config import CREDENTIAL_KEYS
    from kifs.secrets.infisical import push_secret

    if args.secret_action == "check":
        resolve_credentials(settings, use_infisical=not args.no_infisical)
        print(f"source: {settings.credential_source}")
        for key, value in (("WEB_SESSION", settings.web_session),
                           ("ANALYTICS_SESSION", settings.analytics_session),
                           ("SMTP_PASSWORD", settings.smtp_password)):
            print(f"  {key}: {'set (%d chars)' % len(value) if value else 'MISSING'}")
        # These are addresses and hostnames, not secrets; showing them is what
        # makes a misdirected report easy to spot.
        for key, value in (("SMTP_SERVER", settings.smtp_server),
                           ("SMTP_PORT", settings.smtp_port),
                           ("SMTP_FROM", settings.smtp_from),
                           ("REPORT_TO", settings.report_to)):
            print(f"  {key}: {value if value else 'MISSING'}")
        print(f"  mail configured: {settings.mail_configured}")
        return 0 if settings.web_session else 2

    if args.secret_action == "push":
        import os

        from kifs.config import _load_env_files

        _load_env_files()
        pushed = 0
        for key in CREDENTIAL_KEYS:
            value = os.getenv(key)
            if not value:
                print(f"  {key}: not in the environment, skipped")
                continue
            ok = push_secret(key, value)
            print(f"  {key}: {'pushed' if ok else 'FAILED'}")
            pushed += int(ok)
        return 0 if pushed else 1
    return 1


# -- argument parsing -------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kifs", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-infisical", action="store_true",
                        help="Skip Infisical and read credentials from the environment only.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging.")
    parser.add_argument("--no-log-file", action="store_true",
                        help="Log to stdout only (systemd captures it via journald).")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the collector continuously.")
    serve.add_argument("--retry-batch", type=int, default=50,
                       help="Games re-attempted per cycle (default: 50).")
    serve.add_argument("--max-cycles", type=int, default=None,
                       help="Stop after this many cycles (for testing).")
    serve.set_defaults(func=cmd_serve)

    crawl = sub.add_parser("crawl", help="One-shot crawl of N users.")
    crawl.add_argument("--users", type=int, default=10, help="Users to crawl (default: 10).")
    crawl.add_argument("--retry-batch", type=int, default=50)
    crawl.set_defaults(func=cmd_crawl)

    download = sub.add_parser("download", help="Download KIFs for games.")
    download.add_argument("game_ids", nargs="*", help="Specific game ids.")
    download.add_argument("--limit", type=int, default=100,
                          help="With no ids: how many due records to process.")
    download.set_defaults(func=cmd_download)

    recon = sub.add_parser("reconcile", help="Re-sync the database with the KIF files on disk.")
    recon.set_defaults(func=cmd_reconcile)

    search = sub.add_parser("search", help="Search indexed games.")
    search.add_argument("--game-id")
    search.add_argument("--player")
    search.add_argument("--sente")
    search.add_argument("--gote")
    search.add_argument("--result")
    search.add_argument("--min-moves", type=int)
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    stats = sub.add_parser("stats", help="Dataset summary.")
    stats.set_defaults(func=cmd_stats)

    status = sub.add_parser("status", help="Operational snapshot as JSON.")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="Daily collection report.")
    report.add_argument("--send", action="store_true", help="Mail it (what the timer does).")
    report.add_argument("--to", default=None, help="Override the recipient.")
    report.add_argument("--json", action="store_true", help="Print the raw numbers.")
    report.set_defaults(func=cmd_report)

    export = sub.add_parser("export", help="Write the TinyDB-format kifu_db.json.")
    export.add_argument("--output", "-o", default=None, help="Target path.")
    export.add_argument("--games-only", action="store_true",
                        help="Omit the crawl_records table.")
    export.add_argument("--indent", action="store_true", help="Pretty-print (much larger).")
    export.set_defaults(func=cmd_export)

    migrate = sub.add_parser("migrate-sqlite",
                             help="Import a v2 kifu_db.json + frontier.json into SQLite.")
    migrate.add_argument("--json", default=None, help="Source kifu_db.json.")
    migrate.add_argument("--frontier", default=None, help="Source frontier.json.")
    migrate.add_argument("--dry-run", action="store_true", help="Report without writing.")
    migrate.set_defaults(func=cmd_migrate_sqlite)

    ranks = sub.add_parser("ranks", help="Player rank enrichment.")
    ranks.add_argument("rank_action", choices=["fetch", "annotate", "backfill"])
    ranks.add_argument("--limit", type=int, default=None)
    ranks.set_defaults(func=cmd_ranks)

    secrets = sub.add_parser("secrets", help="Inspect or publish credentials.")
    secrets.add_argument("secret_action", choices=["check", "push"])
    secrets.set_defaults(func=cmd_secrets)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(
        settings.log_dir,
        level=logging.DEBUG if args.verbose else logging.INFO,
        to_file=not args.no_log_file,
    )
    return args.func(args, settings)


if __name__ == "__main__":
    sys.exit(main())
