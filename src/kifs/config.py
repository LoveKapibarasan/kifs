"""Single source of truth for settings, paths and credentials.

Every path the project touches is derived from :data:`Settings.data_dir`, so a
deployment only has to set ``KIFS_DATA_DIR`` to relocate the whole dataset.
Credentials are resolved once, here, instead of by five separate
``load_dotenv()`` calls (see issue #5).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Repository root: src/kifs/config.py -> src/kifs -> src -> <root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Ordered credential sources. The first one that yields a value wins.
CREDENTIAL_KEYS = ("WEB_SESSION", "ANALYTICS_SESSION")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Resolved runtime configuration."""

    data_dir: Path
    kif_dir: Path
    db_path: Path
    state_dir: Path
    frontier_path: Path
    log_dir: Path
    lock_path: Path

    # Credentials (resolved lazily by resolve_credentials()).
    web_session: Optional[str] = None
    analytics_session: Optional[str] = None
    credential_source: str = "unresolved"

    # Crawl behaviour -------------------------------------------------
    game_types: List[str] = field(default_factory=lambda: ["sb"])
    #: Stop paging a user's history once a page yields no unseen game id.
    max_history_pages: int = 20
    #: Re-visit a user this many hours after the last crawl (issue #3).
    user_recrawl_hours: float = 24.0
    #: Retry a game whose KIF is not published yet, with exponential backoff.
    kif_retry_base_minutes: float = 30.0
    kif_retry_max_minutes: float = 60.0 * 24.0
    kif_max_attempts: int = 12
    #: Polite delay between outbound requests, in seconds.
    request_delay: float = 1.5
    request_timeout: float = 20.0
    #: Ranking offsets used to seed / re-seed the user frontier.
    seed_offsets: List[int] = field(default_factory=lambda: list(range(1, 501, 25)))
    #: Flush the in-memory database after this many writes or seconds (issue #6).
    flush_every_writes: int = 200
    flush_every_seconds: float = 60.0

    @property
    def infisical_enabled(self) -> bool:
        return bool(os.getenv("INFISICAL_KIFS_CLIENT_ID"))

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.kif_dir, self.state_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)

    def kif_path(self, game_id: str) -> Path:
        return self.kif_dir / f"{game_id}.kif"


def _load_env_files() -> None:
    """Load ``~/.env.global`` then the repository ``.env`` (the latter wins)."""
    global_env = Path(os.getenv("KIFS_GLOBAL_ENV", Path.home() / ".env.global"))
    if global_env.is_file():
        load_dotenv(global_env, override=False)
    local_env = PROJECT_ROOT / ".env"
    if local_env.is_file():
        load_dotenv(local_env, override=False)


def load_settings() -> Settings:
    """Build :class:`Settings` from the environment."""
    _load_env_files()

    data_dir = Path(os.getenv("KIFS_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
    settings = Settings(
        data_dir=data_dir,
        kif_dir=Path(os.getenv("KIFS_KIF_DIR", data_dir / "kif")).expanduser(),
        db_path=Path(os.getenv("KIFS_DB_PATH", data_dir / "kifu_db.json")).expanduser(),
        state_dir=data_dir / "state",
        frontier_path=data_dir / "state" / "frontier.json",
        log_dir=Path(os.getenv("KIFS_LOG_DIR", data_dir / "logs")).expanduser(),
        lock_path=data_dir / "state" / "kifs.lock",
    )

    game_types = os.getenv("KIFS_GAME_TYPES")
    if game_types:
        settings.game_types = [g.strip() for g in game_types.split(",") if g.strip()]

    settings.max_history_pages = _env_int("KIFS_MAX_HISTORY_PAGES", settings.max_history_pages)
    settings.user_recrawl_hours = _env_float("KIFS_USER_RECRAWL_HOURS", settings.user_recrawl_hours)
    settings.kif_retry_base_minutes = _env_float("KIFS_KIF_RETRY_BASE_MINUTES", settings.kif_retry_base_minutes)
    settings.kif_retry_max_minutes = _env_float("KIFS_KIF_RETRY_MAX_MINUTES", settings.kif_retry_max_minutes)
    settings.kif_max_attempts = _env_int("KIFS_KIF_MAX_ATTEMPTS", settings.kif_max_attempts)
    settings.request_delay = _env_float("KIFS_REQUEST_DELAY", settings.request_delay)
    settings.request_timeout = _env_float("KIFS_REQUEST_TIMEOUT", settings.request_timeout)
    settings.flush_every_writes = _env_int("KIFS_FLUSH_EVERY_WRITES", settings.flush_every_writes)
    settings.flush_every_seconds = _env_float("KIFS_FLUSH_EVERY_SECONDS", settings.flush_every_seconds)

    seed_max = _env_int("KIFS_SEED_MAX_OFFSET", 500)
    settings.seed_offsets = list(range(1, seed_max + 1, 25))

    return settings


def resolve_credentials(settings: Settings, use_infisical: bool = True) -> Settings:
    """Fill in session cookies from Infisical, falling back to the environment.

    Resolution order is Infisical -> environment/.env, and the winning source is
    recorded in ``settings.credential_source`` so the service can log it.
    """
    resolved: dict = {}
    source = "env"

    if use_infisical and settings.infisical_enabled:
        from kifs.secrets.infisical import fetch_secrets

        try:
            resolved = fetch_secrets(CREDENTIAL_KEYS)
            if resolved:
                source = "infisical"
        except Exception as exc:  # pragma: no cover - network dependent
            import logging

            logging.getLogger(__name__).warning(
                "Infisical lookup failed (%s); falling back to environment.", exc
            )
            resolved = {}

    for key in CREDENTIAL_KEYS:
        if not resolved.get(key):
            env_value = os.getenv(key)
            if env_value:
                resolved[key] = env_value
                if source == "infisical":
                    source = "infisical+env"

    settings.web_session = resolved.get("WEB_SESSION")
    settings.analytics_session = resolved.get("ANALYTICS_SESSION")
    settings.credential_source = source if resolved else "missing"
    return settings
