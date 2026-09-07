"""Push collected KIF files to the Silo object store.

Other applications read the KIFs from object storage rather than from the
collector's disk, so every downloaded ``.kif`` is uploaded under
``<prefix>/<game_id>.kif``.

Which files still need uploading is tracked in ``crawl_records.uploaded_at``,
so a run costs one indexed query rather than a full bucket listing — the same
reason the retry scheduler has its own index. ``--verify`` exists for the case
where that local state and the bucket have drifted apart.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from kifs.clients.s3 import S3Client, S3Config, S3Error
from kifs.config import Settings
from kifs.storage.database import STATUS_INDEXED, KifuDatabase, isoformat, utcnow

log = logging.getLogger(__name__)


def s3_config(settings: Settings) -> S3Config:
    return S3Config(
        endpoint=settings.s3_endpoint or "",
        bucket=settings.s3_bucket or "",
        access_key=settings.s3_access_key or "",
        secret_key=settings.s3_secret_key or "",
        region=settings.s3_region,
    )


def object_key(settings: Settings, game_id: str) -> str:
    prefix = settings.s3_prefix.strip("/")
    return f"{prefix}/{game_id}.kif" if prefix else f"{game_id}.kif"


def pending_game_ids(db: KifuDatabase, limit: Optional[int] = None) -> List[str]:
    """Indexed games whose KIF has not been uploaded yet, oldest first."""
    query = (
        "SELECT game_id FROM crawl_records "
        "WHERE uploaded_at IS NULL AND kif_status = ? AND has_kif_file = 1 "
        "ORDER BY discovered_at ASC"
    )
    params: list = [STATUS_INDEXED]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    return [row["game_id"] for row in db.connection.execute(query, params)]


def mark_uploaded(db: KifuDatabase, game_ids: List[str]) -> None:
    if not game_ids:
        return
    stamp = isoformat(utcnow())
    db.transaction.begin()
    db.connection.executemany(
        "UPDATE crawl_records SET uploaded_at = ? WHERE game_id = ?",
        [(stamp, game_id) for game_id in game_ids],
    )
    db.transaction.mark(len(game_ids))


def sync(settings: Settings, db: KifuDatabase, limit: Optional[int] = None,
         verify: bool = False, dry_run: bool = False) -> Dict[str, int]:
    """Upload every KIF that object storage does not have yet."""
    counts = {"pending": 0, "uploaded": 0, "already": 0, "missing_file": 0, "failed": 0}

    if not settings.s3_configured:
        raise S3Error("S3 is not configured; expected S3_ENDPOINT / S3_BUCKET / "
                      "S3_ACCESS_KEY / S3_SECRET_KEY from Infisical.")

    if verify:
        reconcile_upload_state(settings, db)

    targets = pending_game_ids(db, limit or settings.s3_batch)
    counts["pending"] = len(targets)
    if not targets:
        log.info("Object storage is up to date; nothing to upload.")
        return counts
    log.info("Uploading %d KIF file(s) to %s/%s.",
             len(targets), settings.s3_endpoint, settings.s3_bucket)
    if dry_run:
        return counts

    done: List[str] = []
    with S3Client(s3_config(settings)) as client:
        for game_id in targets:
            path = settings.kif_path(game_id)
            if not path.is_file():
                # reconcile() owns re-downloading; just do not claim it is uploaded.
                counts["missing_file"] += 1
                continue
            try:
                client.put_object(object_key(settings, game_id),
                                  path.read_bytes(), content_type="text/plain")
            except (S3Error, OSError) as exc:
                counts["failed"] += 1
                log.warning("Upload failed for %s: %s", game_id, exc)
                # Leave uploaded_at NULL so the next run retries it.
                continue
            done.append(game_id)
            counts["uploaded"] += 1
            if len(done) >= 500:
                mark_uploaded(db, done)
                db.flush(force=True)
                log.info("  uploaded %d/%d", counts["uploaded"], len(targets))
                done = []

    mark_uploaded(db, done)
    db.flush(force=True)
    log.info("Upload finished: %(uploaded)d uploaded, %(failed)d failed, "
             "%(missing_file)d without a local file.", counts)
    return counts


def reconcile_upload_state(settings: Settings, db: KifuDatabase) -> Dict[str, int]:
    """Re-derive ``uploaded_at`` from what the bucket actually holds.

    The local flag can drift: a bucket emptied by hand, or a crash between the
    PUT and the flag being written. Listing is expensive, so this is opt-in
    (``kifs sync --verify``) rather than part of every run.
    """
    counts = {"in_bucket": 0, "marked": 0, "cleared": 0}
    prefix = settings.s3_prefix.strip("/")
    prefix_with_slash = f"{prefix}/" if prefix else ""

    with S3Client(s3_config(settings)) as client:
        in_bucket = set()
        for key, size in client.list_objects(prefix_with_slash):
            if key.endswith(".kif") and size > 0:
                in_bucket.add(key[len(prefix_with_slash):-len(".kif")])
    counts["in_bucket"] = len(in_bucket)
    log.info("Bucket holds %d KIF object(s).", len(in_bucket))

    marked = [row["game_id"] for row in db.connection.execute(
        "SELECT game_id FROM crawl_records WHERE uploaded_at IS NOT NULL")]
    marked_set = set(marked)

    to_clear = [g for g in marked if g not in in_bucket]
    to_mark = [g for g in in_bucket if g not in marked_set]

    if to_clear:
        db.transaction.begin()
        db.connection.executemany(
            "UPDATE crawl_records SET uploaded_at = NULL WHERE game_id = ?",
            [(g,) for g in to_clear])
        db.transaction.mark(len(to_clear))
        counts["cleared"] = len(to_clear)
    if to_mark:
        mark_uploaded(db, to_mark)
        counts["marked"] = len(to_mark)
    db.flush(force=True)

    log.info("Upload state verified: %d re-queued, %d already in the bucket.",
             counts["cleared"], counts["marked"])
    return counts
