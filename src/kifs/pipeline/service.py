"""The long-running collector (issue #2).

Each cycle does three things, in this order:

1. **retry backlog** — games discovered earlier whose KIF was not published
   yet and are now due for another attempt;
2. **crawl one user** — the next unseen user, or the one longest overdue for a
   revisit, registering every game they played;
3. **collect that user's games** — download and index anything not already held.

When the frontier has nothing due, the ranking is re-seeded rather than the
loop exiting, which is the bug that let v1 stop silently once its queue drained.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from kifs.clients.kishin import KishinAnalyticsClient
from kifs.clients.shogiwars import AuthenticationError, ShogiWarsClient
from kifs.config import Settings
from kifs.notify.alerts import (
    ALERT_COOKIE_EXPIRED,
    ALERT_CYCLE_FAILING,
    ALERT_STALLED,
    Alerter,
)
from kifs.pipeline.discovery import Discovery
from kifs.pipeline.downloader import Downloader
from kifs.pipeline.indexer import reconcile
from kifs.storage.database import KifuDatabase
from kifs.storage.frontier import Frontier
from kifs.storage.lock import ProcessLock

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


@dataclass
class Counters:
    users_crawled: int = 0
    games_discovered: int = 0
    games_indexed: int = 0
    games_retry_scheduled: int = 0
    retries_attempted: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_index_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        rate = self.games_indexed / elapsed * 3600 if elapsed > 0 else 0.0
        return (f"users={self.users_crawled} discovered={self.games_discovered} "
                f"indexed={self.games_indexed} retry={self.games_retry_scheduled} "
                f"({rate:.0f} games/h)")


class CollectorService:
    def __init__(self, settings: Settings, retry_batch: int = 50,
                 max_cycles: Optional[int] = None):
        self.settings = settings
        self.retry_batch = retry_batch
        self.max_cycles = max_cycles
        self.running = True
        self.counters = Counters()
        self.db = KifuDatabase(
            settings.db_path,
            flush_every_writes=settings.flush_every_writes,
            flush_every_seconds=settings.flush_every_seconds,
        )
        self.frontier = Frontier(
            settings.frontier_path,
            recrawl_hours=settings.user_recrawl_hours,
            flush_every_seconds=settings.flush_every_seconds,
        )
        self.alerter = Alerter(settings)
        self._consecutive_failures = 0

    # -- lifecycle ----------------------------------------------------
    def request_shutdown(self) -> None:
        if self.running:
            log.info("Shutdown requested; finishing the current game then saving state.")
        self.running = False

    def _persist(self, force: bool = False) -> None:
        self.db.flush(force=force)
        self.frontier.save(force=force)

    async def run(self) -> None:
        self.settings.ensure_dirs()
        with ProcessLock(self.settings.lock_path):
            self.db.open()
            self.frontier.load()
            reconcile(self.db, self.settings)
            self._persist(force=True)

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.request_shutdown)
                except NotImplementedError:  # pragma: no cover - non-unix
                    pass

            try:
                await self._run_forever()
            finally:
                self._persist(force=True)
                self.db.close()
                log.info("Stopped. %s", self.counters.summary())

    async def _run_forever(self) -> None:
        """Reconnect on transport failure instead of dying (v1's `main` retry)."""
        backoff = 5.0
        while self.running:
            try:
                await self._session()
                backoff = 5.0
                self._consecutive_failures = 0
            except AuthenticationError as exc:
                log.error("Session cookie rejected (%s). Credentials came from '%s'. "
                          "Refresh WEB_SESSION in Infisical, then restart.",
                          exc, self.settings.credential_source)
                self.alerter.fire(
                    ALERT_COOKIE_EXPIRED,
                    "セッションCookieが拒否されました",
                    f"将棋ウォーズのセッションCookieが拒否されました ({exc})。\n"
                    f"認証情報の取得元: {self.settings.credential_source}\n\n"
                    "収集は停止していませんが、Cookieを更新するまで新規対局は集まりません。\n"
                    "対処:\n"
                    "  1. ブラウザから新しい _web_session / sessionid を取得\n"
                    "  2. export WEB_SESSION=... ANALYTICS_SESSION=... && kifs secrets push\n"
                    "  3. systemctl --user restart kifs-collector",
                )
                self._persist(force=True)
                await self._sleep(min(backoff * 12, 900.0))
                backoff = min(backoff * 2, 300.0)
            except Exception as exc:
                self._consecutive_failures += 1
                log.exception("Cycle failed (%s); retrying in %.0fs.", exc, backoff)
                if self._consecutive_failures >= 5:
                    self.alerter.fire(
                        ALERT_CYCLE_FAILING,
                        "収集サイクルが連続で失敗しています",
                        f"収集サイクルが {self._consecutive_failures} 回連続で失敗しました。\n"
                        f"直近の例外: {type(exc).__name__}: {exc}",
                    )
                self._persist(force=True)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 300.0)

    async def _session(self) -> None:
        headers = {"User-Agent": USER_AGENT}
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
        async with httpx.AsyncClient(headers=headers, timeout=self.settings.request_timeout,
                                     limits=limits) as client:
            wars = ShogiWarsClient(client, self.settings.web_session)
            kishin = KishinAnalyticsClient(
                client, self.settings.analytics_session, self.settings.web_session)
            if not kishin.authenticated:
                log.warning("ANALYTICS_SESSION is not set — KIF downloads will fail.")

            discovery = Discovery(wars, self.db, self.frontier, self.settings)
            downloader = Downloader(kishin, self.db, self.settings)

            cycles = 0
            while self.running:
                await self._cycle(discovery, downloader)
                self._check_stalled()
                cycles += 1
                if self.max_cycles is not None and cycles >= self.max_cycles:
                    log.info("Reached max_cycles=%d; stopping.", self.max_cycles)
                    self.running = False
                self._persist()

    # -- one unit of work ---------------------------------------------
    async def _cycle(self, discovery: Discovery, downloader: Downloader) -> None:
        await self._drain_retries(downloader)
        if not self.running:
            return

        user_id = self.frontier.next_user()
        if user_id is None:
            # Nothing due: top up from the ranking rather than exiting (issue #2).
            log.info("Frontier has nothing due; re-seeding from the ranking.")
            await discovery.seed_from_rankings()
            if self.frontier.next_user() is None:
                log.info("Still nothing due; sleeping 60s before looking again.")
                await self._sleep(60.0)
            return

        games, new_count = await discovery.crawl_user(user_id)
        self.counters.users_crawled += 1
        self.counters.games_discovered += new_count
        indexed_here = 0

        for game_id, game_type in games:
            if not self.running:
                break
            if self.db.has_game(game_id):
                continue
            outcome = await downloader.collect(game_id, user_id, game_type)
            if outcome == "indexed":
                indexed_here += 1
                self.counters.games_indexed += 1
                self.counters.last_index_at = datetime.now(timezone.utc)
                # Traffic is flowing again; let the next problem alert at once.
                self.alerter.clear(ALERT_COOKIE_EXPIRED)
                self.alerter.clear(ALERT_STALLED)
            elif outcome == "retry":
                self.counters.games_retry_scheduled += 1
            await self._sleep(self.settings.request_delay)

        self.frontier.mark_crawled(user_id, new_count)
        log.info(
            "%s: %d games seen, %d new, %d indexed | frontier %d known / %d unseen | %s",
            user_id, len(games), new_count, indexed_here,
            len(self.frontier), self.frontier.never_crawled, self.counters.summary(),
        )

    def _check_stalled(self) -> None:
        """Alert when the collector is running but nothing is being collected.

        A silent no-op loop looks identical to a healthy one in `systemctl
        status`, so this is the condition most worth mailing about.
        """
        idle_minutes = (datetime.now(timezone.utc)
                        - self.counters.last_index_at).total_seconds() / 60
        if idle_minutes < self.settings.stall_minutes:
            return
        self.alerter.fire(
            ALERT_STALLED,
            f"収集が {idle_minutes:.0f} 分停止しています",
            f"サービスは稼働中ですが、直近 {idle_minutes:.0f} 分で1件も索引化されていません。\n\n"
            f"巡回済みユーザー: {self.counters.users_crawled}\n"
            f"発見した対局    : {self.counters.games_discovered}\n"
            f"索引化した対局  : {self.counters.games_indexed}\n"
            f"再試行に回した数: {self.counters.games_retry_scheduled}\n\n"
            "Cookieの失効、上流のレート制限、巡回対象ユーザーの枯渇などが考えられます。",
        )

    async def _drain_retries(self, downloader: Downloader) -> None:
        """Re-attempt games whose KIF was unpublished when we first saw them."""
        due = self.db.due_records(limit=self.retry_batch)
        due = [record for record in due if not self.db.has_game(record.game_id)]
        if not due:
            return
        log.info("Retrying %d game(s) whose KIF was not available before.", len(due))
        for record in due:
            if not self.running:
                break
            self.counters.retries_attempted += 1
            outcome = await downloader.collect(
                record.game_id, record.get("source_user"), record.get("game_type"))
            if outcome == "indexed":
                self.counters.games_indexed += 1
                self.counters.last_index_at = datetime.now(timezone.utc)
            await self._sleep(self.settings.request_delay)

    async def _sleep(self, seconds: float) -> None:
        """Sleep in short slices so SIGTERM is honoured promptly."""
        remaining = seconds
        while remaining > 0 and self.running:
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step
