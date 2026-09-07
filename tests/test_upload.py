"""Object-storage sync: signing, upload state, and drift recovery."""
import httpx
import pytest

from kifs.clients.s3 import S3Client, S3Config, S3Error
from kifs.pipeline.upload import (
    object_key,
    pending_game_ids,
    reconcile_upload_state,
    sync,
)
from kifs.storage.database import KifuDatabase


class FakeBucket:
    """An in-memory S3 that checks the requests actually look signed."""

    def __init__(self, fail_keys=()):
        self.objects: dict[str, bytes] = {}
        self.fail_keys = set(fail_keys)
        self.requests: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        # raw_path is what actually goes on the wire, and it is what the
        # signature was computed over; request.url.path is decoded.
        self.requests.append((request.method, request.url.raw_path.decode()))
        auth = request.headers.get("authorization", "")
        assert auth.startswith("AWS4-HMAC-SHA256 Credential="), "request was not signed"
        assert "x-amz-date" in request.headers
        assert "x-amz-content-sha256" in request.headers

        # /<bucket>/<key...>
        _, bucket, *rest = request.url.path.split("/")
        key = "/".join(rest)  # url.path is already decoded

        if request.method == "PUT":
            if key in self.fail_keys:
                return httpx.Response(500, text="<Error>boom</Error>")
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "HEAD":
            if not key:
                return httpx.Response(200)
            if key not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, headers={"content-length": str(len(self.objects[key]))})
        if request.method == "GET":
            prefix = request.url.params.get("prefix", "")
            contents = "".join(
                f"<Contents><Key>{k}</Key><Size>{len(v)}</Size></Contents>"
                for k, v in sorted(self.objects.items()) if k.startswith(prefix))
            return httpx.Response(200, text=(
                "<ListBucketResult>"
                f"<IsTruncated>false</IsTruncated>{contents}"
                "</ListBucketResult>"))
        return httpx.Response(405)


@pytest.fixture
def bucket():
    return FakeBucket()


@pytest.fixture
def s3_settings(settings):
    settings.s3_endpoint = "http://silo.test:9002"
    settings.s3_bucket = "kifs"
    settings.s3_access_key = "kifs-writer"
    settings.s3_secret_key = "secret"
    settings.s3_prefix = "kif"
    return settings


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch, bucket):
    """Route every S3Client through the fake bucket."""
    import kifs.clients.s3 as s3_module

    original = s3_module.S3Client.__init__

    def patched(self, config, client=None, timeout=60.0):
        original(self, config,
                 client=httpx.Client(transport=httpx.MockTransport(bucket.handler)),
                 timeout=timeout)
        self._owns_client = True

    monkeypatch.setattr(s3_module.S3Client, "__init__", patched)


def _seed(settings, game_ids, write_files=True):
    db = KifuDatabase(settings.db_path, flush_every_writes=10_000).open()
    for game_id in game_ids:
        if write_files:
            settings.kif_path(game_id).write_text(f"先手：{game_id}\n", encoding="utf-8")
        db.add_record(game_id, "sb", "u")
        db.mark_indexed(game_id)
    db.flush(force=True)
    return db


# -- keys and selection ------------------------------------------------
def test_object_key_uses_the_prefix(s3_settings):
    assert object_key(s3_settings, "a-b-1") == "kif/a-b-1.kif"
    s3_settings.s3_prefix = ""
    assert object_key(s3_settings, "a-b-1") == "a-b-1.kif"


def test_only_indexed_games_with_a_file_are_pending(s3_settings):
    db = _seed(s3_settings, ["a-b-1"])
    db.add_record("not-downloaded", "sb", "u")   # kif_missing
    db.flush(force=True)
    assert pending_game_ids(db) == ["a-b-1"]
    db.close()


# -- upload ------------------------------------------------------------
def test_sync_uploads_and_records_it(s3_settings, bucket):
    db = _seed(s3_settings, ["a-b-1", "c-d-2"])
    counts = sync(s3_settings, db)
    assert counts["uploaded"] == 2
    assert set(bucket.objects) == {"kif/a-b-1.kif", "kif/c-d-2.kif"}
    assert bucket.objects["kif/a-b-1.kif"] == b"\xe5\x85\x88\xe6\x89\x8b\xef\xbc\x9aa-b-1\n"
    assert pending_game_ids(db) == [], "uploaded files must not be re-sent"
    db.close()


def test_sync_is_incremental(s3_settings, bucket):
    db = _seed(s3_settings, ["a-b-1"])
    sync(s3_settings, db)
    before = len(bucket.requests)

    settings_path = s3_settings.kif_path("c-d-2")
    settings_path.write_text("先手：c\n", encoding="utf-8")
    db.add_record("c-d-2", "sb", "u")
    db.mark_indexed("c-d-2")
    db.flush(force=True)

    counts = sync(s3_settings, db)
    assert counts["uploaded"] == 1, "only the new file goes up"
    assert len(bucket.requests) == before + 1
    db.close()


def test_a_failed_upload_is_retried_next_run(s3_settings, bucket):
    """A failure must not mark the game as uploaded, or it would be lost."""
    bucket.fail_keys = {"kif/c-d-2.kif"}
    db = _seed(s3_settings, ["a-b-1", "c-d-2"])
    counts = sync(s3_settings, db)
    assert counts["uploaded"] == 1 and counts["failed"] == 1
    assert pending_game_ids(db) == ["c-d-2"]

    bucket.fail_keys = set()
    assert sync(s3_settings, db)["uploaded"] == 1
    assert pending_game_ids(db) == []
    db.close()


def test_missing_local_file_is_not_marked_uploaded(s3_settings):
    db = _seed(s3_settings, ["a-b-1"], write_files=False)
    counts = sync(s3_settings, db)
    assert counts["missing_file"] == 1 and counts["uploaded"] == 0
    assert pending_game_ids(db) == ["a-b-1"]
    db.close()


def test_limit_bounds_one_run(s3_settings):
    db = _seed(s3_settings, [f"g-{i}-1" for i in range(5)])
    assert sync(s3_settings, db, limit=2)["uploaded"] == 2
    assert len(pending_game_ids(db)) == 3
    db.close()


def test_dry_run_uploads_nothing(s3_settings, bucket):
    db = _seed(s3_settings, ["a-b-1"])
    counts = sync(s3_settings, db, dry_run=True)
    assert counts["pending"] == 1 and counts["uploaded"] == 0
    assert bucket.objects == {}
    db.close()


def test_unconfigured_s3_raises(settings):
    db = KifuDatabase(settings.db_path).open()
    with pytest.raises(S3Error, match="not configured"):
        sync(settings, db)
    db.close()


# -- drift -------------------------------------------------------------
def test_verify_requeues_objects_missing_from_the_bucket(s3_settings, bucket):
    """If the bucket is emptied, the local 'uploaded' flags are wrong."""
    db = _seed(s3_settings, ["a-b-1", "c-d-2"])
    sync(s3_settings, db)
    bucket.objects.clear()

    counts = reconcile_upload_state(s3_settings, db)
    assert counts["cleared"] == 2
    assert len(pending_game_ids(db)) == 2
    db.close()


def test_verify_adopts_objects_already_in_the_bucket(s3_settings, bucket):
    """A crash between the PUT and the flag would otherwise re-upload."""
    db = _seed(s3_settings, ["a-b-1"])
    bucket.objects["kif/a-b-1.kif"] = b"already there"

    counts = reconcile_upload_state(s3_settings, db)
    assert counts["marked"] == 1
    assert pending_game_ids(db) == []
    db.close()


# -- signing -----------------------------------------------------------
def test_signature_covers_the_payload(bucket):
    """Two different payloads must not produce the same signature."""
    config = S3Config(endpoint="http://silo.test:9002", bucket="kifs",
                      access_key="k", secret_key="s")
    signatures = []
    for payload in (b"one", b"two"):
        with S3Client(config) as client:
            client.put_object("k.kif", payload)
        signatures.append(bucket.objects["k.kif"])
    assert signatures[0] != signatures[1]


def test_keys_with_special_characters_are_escaped(bucket):
    """The signature is computed over the encoded path, so the wire path must
    match it exactly — no double encoding, no leaving spaces raw."""
    config = S3Config(endpoint="http://silo.test:9002", bucket="kifs",
                      access_key="k", secret_key="s")
    with S3Client(config) as client:
        client.put_object("kif/a b+c-1.kif", b"x")
    paths = [path for _, path in bucket.requests]
    assert "/kifs/kif/a%20b%2Bc-1.kif" in paths
    assert not any("%25" in path for path in paths), "double-encoded"


def test_collector_uploads_as_part_of_its_cycle(s3_settings, bucket, monkeypatch):
    """Uploads run inside the collector because SQLite takes a single writer;
    a separate sync process would only wait for the collector's write lock."""
    from kifs.pipeline.service import CollectorService

    service = CollectorService(s3_settings, upload_batch=10)
    service.db.open()
    for game_id in ("a-b-1", "c-d-2"):
        s3_settings.kif_path(game_id).write_text("先手：x\n", encoding="utf-8")
        service.db.add_record(game_id, "sb", "u")
        service.db.mark_indexed(game_id)
    service.db.flush(force=True)

    service._drain_uploads()

    assert set(bucket.objects) == {"kif/a-b-1.kif", "kif/c-d-2.kif"}
    assert service.counters.games_uploaded == 2
    service.db.close()


def test_object_storage_being_down_does_not_stop_collection(s3_settings, monkeypatch):
    from kifs.pipeline.service import CollectorService

    def boom(*args, **kwargs):
        raise RuntimeError("silo unreachable")

    monkeypatch.setattr("kifs.pipeline.service.upload_sync", boom)
    service = CollectorService(s3_settings)
    service.db.open()
    service._drain_uploads()          # must not raise
    assert service.counters.games_uploaded == 0
    service.db.close()


def test_uploads_are_skipped_when_s3_is_unconfigured(settings, bucket):
    from kifs.pipeline.service import CollectorService

    service = CollectorService(settings)
    service.db.open()
    service._drain_uploads()
    assert bucket.objects == {}
    service.db.close()
