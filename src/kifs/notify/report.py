"""The daily collection report.

The interesting number is not the total but the *delta*: how many games were
added since the last report, and whether anything is stuck. The previous
snapshot is kept in ``data/state/report_state.json``, so a report is always
"since you last heard from me" even if a run is skipped.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from html import escape
from typing import Dict, Optional

import orjson

from kifs.config import Settings
from kifs.storage.database import (
    STATUS_KIF_MISSING,
    STATUS_KIF_UNAVAILABLE,
    KifuDatabase,
    parse_iso,
)
from kifs.storage.frontier import Frontier

log = logging.getLogger(__name__)


def _load_snapshot(settings: Settings) -> Optional[dict]:
    path = settings.report_state_path
    if path.is_file() and path.stat().st_size > 0:
        try:
            return orjson.loads(path.read_bytes())
        except ValueError:
            return None
    return None


def _save_snapshot(settings: Settings, snapshot: dict) -> None:
    path = settings.report_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(orjson.dumps(snapshot))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def build_report(settings: Settings, persist: bool = True) -> dict:
    """Gather the numbers for one report, and record them as the new baseline."""
    # The collector is normally running; a writer open would block on its lock.
    db = KifuDatabase(settings.db_path, read_only=True).open()
    frontier = Frontier(db, recrawl_hours=settings.user_recrawl_hours)
    try:
        counts = db.status_counts()
        now = datetime.now(timezone.utc)
        current = {
            "at": now.isoformat(),
            "games_indexed": db.count_games(),
            "crawl_records": db.count_records(),
            "users_known": len(frontier),
            "db_size_mb": round(db.size_bytes() / 1e6, 1),
        }

        previous = _load_snapshot(settings)
        delta: Dict[str, Optional[float]] = {}
        hours = None
        if previous:
            previous_at = parse_iso(previous.get("at"))
            if previous_at:
                hours = (now - previous_at).total_seconds() / 3600
            for key in ("games_indexed", "crawl_records", "users_known"):
                delta[key] = current[key] - previous.get(key, 0)

        report = {
            "generated_at": now.isoformat(),
            "since": previous.get("at") if previous else None,
            "hours_covered": round(hours, 1) if hours else None,
            "current": current,
            "delta": delta,
            "games_per_hour": round(delta["games_indexed"] / hours, 1)
            if delta.get("games_indexed") is not None and hours else None,
            "records_by_status": counts,
            # due_records() also covers records that were downloaded but never
            # indexed, so it is not a subset of kif_missing — report it as its
            # own number rather than "of which".
            "records_due_now": len(db.due_records()),
            "records_given_up": counts.get(STATUS_KIF_UNAVAILABLE, 0),
            "retry_backlog": counts.get(STATUS_KIF_MISSING, 0),
            "users_never_crawled": frontier.never_crawled,
            "users_due": frontier.due_count(),
            "warnings": [],
        }

        if delta.get("games_indexed") == 0 and hours:
            report["warnings"].append(
                f"直近 {hours:.1f} 時間で新規対局が0件です。収集が止まっている可能性があります。")
        if report["records_given_up"] > 0:
            report["warnings"].append(
                f"再試行上限に達した対局が {report['records_given_up']} 件あります "
                f"(kif_unavailable)。ANALYTICS_SESSION の失効を確認してください。")
        if report["users_due"] == 0:
            report["warnings"].append(
                "巡回対象のユーザーが0件です。ランキング再シードが機能しているか確認してください。")

        if persist:
            _save_snapshot(settings, current)
        return report
    finally:
        db.close()


def _fmt_delta(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"+{value:,}" if value >= 0 else f"{value:,}"


def render_text(report: dict) -> str:
    current = report["current"]
    delta = report["delta"]
    lines = [
        "将棋ウォーズ 棋譜収集 日次レポート",
        "=" * 40,
        f"生成: {report['generated_at']}",
    ]
    if report["hours_covered"]:
        lines.append(f"対象期間: 直近 {report['hours_covered']} 時間")
    lines += [
        "",
        "■ 収集状況",
        f"  索引済み対局   : {current['games_indexed']:,}  ({_fmt_delta(delta.get('games_indexed'))})",
        f"  crawl records  : {current['crawl_records']:,}  ({_fmt_delta(delta.get('crawl_records'))})",
        f"  既知ユーザー   : {current['users_known']:,}  ({_fmt_delta(delta.get('users_known'))})",
        f"  DBサイズ       : {current['db_size_mb']} MB",
    ]
    if report["games_per_hour"] is not None:
        lines.append(f"  収集レート     : {report['games_per_hour']:,} 対局/時")
    lines += [
        "",
        "■ 未処理",
        f"  KIF未取得      : {report['retry_backlog']:,}",
        f"  再試行可能     : {report['records_due_now']:,}",
        f"  取得断念       : {report['records_given_up']:,}",
        f"  未巡回ユーザー : {report['users_never_crawled']:,}",
        f"  巡回対象       : {report['users_due']:,}",
    ]
    if report["warnings"]:
        lines += ["", "■ 警告"]
        lines += [f"  - {warning}" for warning in report["warnings"]]
    else:
        lines += ["", "■ 警告  なし"]
    return "\n".join(lines) + "\n"


def render_html(report: dict) -> str:
    current = report["current"]
    delta = report["delta"]

    def row(label: str, value: str, change: Optional[float] = None) -> str:
        change_cell = ""
        if change is not None:
            colour = "#1a7f37" if change > 0 else "#57606a"
            change_cell = (f'<td style="padding:6px 12px;color:{colour};'
                           f'text-align:right;">{escape(_fmt_delta(change))}</td>')
        else:
            change_cell = '<td style="padding:6px 12px;"></td>'
        return (f'<tr><td style="padding:6px 12px;color:#57606a;">{escape(label)}</td>'
                f'<td style="padding:6px 12px;text-align:right;font-weight:600;">'
                f'{escape(value)}</td>{change_cell}</tr>')

    warnings_html = ""
    if report["warnings"]:
        items = "".join(f"<li style='margin:4px 0;'>{escape(w)}</li>"
                        for w in report["warnings"])
        warnings_html = (
            '<div style="margin-top:20px;padding:12px 16px;background:#fff8c5;'
            'border-left:4px solid #d4a72c;border-radius:4px;">'
            '<strong style="color:#7d4e00;">警告</strong>'
            f'<ul style="margin:8px 0 0;padding-left:20px;color:#7d4e00;">{items}</ul></div>')

    period = (f"直近 {report['hours_covered']} 時間"
              if report["hours_covered"] else "初回レポート")
    rate = (f"{report['games_per_hour']:,} 対局/時"
            if report["games_per_hour"] is not None else "-")

    return f"""<!doctype html>
<html lang="ja"><body style="margin:0;padding:24px;background:#f6f8fa;
 font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Noto Sans JP',sans-serif;">
<div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #d0d7de;
 border-radius:8px;padding:24px;">
  <h1 style="margin:0 0 4px;font-size:18px;color:#1f2328;">棋譜収集 日次レポート</h1>
  <p style="margin:0 0 20px;color:#57606a;font-size:13px;">{escape(period)}
     &middot; {escape(report['generated_at'][:19].replace('T', ' '))} UTC</p>

  <h2 style="margin:0 0 8px;font-size:14px;color:#1f2328;">収集状況</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;
   border:1px solid #d0d7de;border-radius:6px;">
    {row("索引済み対局", f"{current['games_indexed']:,}", delta.get("games_indexed"))}
    {row("crawl records", f"{current['crawl_records']:,}", delta.get("crawl_records"))}
    {row("既知ユーザー", f"{current['users_known']:,}", delta.get("users_known"))}
    {row("収集レート", rate)}
    {row("DBサイズ", f"{current['db_size_mb']} MB")}
  </table>

  <h2 style="margin:20px 0 8px;font-size:14px;color:#1f2328;">未処理</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;
   border:1px solid #d0d7de;border-radius:6px;">
    {row("KIF未取得", f"{report['retry_backlog']:,}")}
    {row("再試行可能", f"{report['records_due_now']:,}")}
    {row("取得断念", f"{report['records_given_up']:,}")}
    {row("未巡回ユーザー", f"{report['users_never_crawled']:,}")}
    {row("巡回対象", f"{report['users_due']:,}")}
  </table>
  {warnings_html}
  <p style="margin:20px 0 0;color:#8c959f;font-size:12px;">
    kifs collector &middot; 172.25.20.20 &middot; systemctl --user status kifs-collector
  </p>
</div></body></html>"""
