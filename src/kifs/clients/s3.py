"""A minimal S3 client for the Silo (MinIO-compatible) object store.

Only what the sync needs: PUT an object, HEAD one, list a prefix. Signing is
AWS SigV4, written out here rather than pulling in boto3 — the collector's whole
dependency set is httpx + orjson + python-dotenv, and this keeps it that way.

Path-style addressing is used throughout (``<endpoint>/<bucket>/<key>``);
MinIO serves both, and path style avoids needing per-bucket DNS.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_ALGORITHM = "AWS4-HMAC-SHA256"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    key = _sign(f"AWS4{secret}".encode("utf-8"), date_stamp)
    key = _sign(key, region)
    key = _sign(key, service)
    return _sign(key, "aws4_request")


@dataclass
class S3Config:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.bucket and self.access_key and self.secret_key)


class S3Error(RuntimeError):
    pass


class S3Client:
    def __init__(self, config: S3Config, client: Optional[httpx.Client] = None,
                 timeout: float = 60.0):
        self.config = config
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "S3Client":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- signing ------------------------------------------------------
    def _request(self, method: str, key: str = "", *, payload: bytes = b"",
                 query: Optional[Dict[str, str]] = None,
                 headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        config = self.config
        endpoint = config.endpoint.rstrip("/")
        host = re.sub(r"^https?://", "", endpoint).split("/")[0]

        # Each path segment is escaped, but "/" separators are kept.
        encoded_key = "/".join(quote(part, safe="") for part in key.split("/")) if key else ""
        canonical_uri = f"/{quote(config.bucket, safe='')}"
        if encoded_key:
            canonical_uri += f"/{encoded_key}"

        query = query or {}
        canonical_query = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}"
            for k, v in sorted(query.items())
        )

        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = _sha256(payload) if payload else _EMPTY_SHA256

        signed_headers_map = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        for name, value in (headers or {}).items():
            signed_headers_map[name.lower()] = value

        signed_names = sorted(signed_headers_map)
        canonical_headers = "".join(
            f"{name}:{signed_headers_map[name].strip()}\n" for name in signed_names)
        signed_headers = ";".join(signed_names)

        canonical_request = "\n".join([
            method, canonical_uri, canonical_query,
            canonical_headers, signed_headers, payload_hash,
        ])
        scope = f"{date_stamp}/{config.region}/s3/aws4_request"
        string_to_sign = "\n".join([
            _ALGORITHM, amz_date, scope, _sha256(canonical_request.encode("utf-8")),
        ])
        signature = hmac.new(
            _signing_key(config.secret_key, date_stamp, config.region, "s3"),
            string_to_sign.encode("utf-8"), hashlib.sha256,
        ).hexdigest()

        request_headers = dict(signed_headers_map)
        request_headers["Authorization"] = (
            f"{_ALGORITHM} Credential={config.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        url = f"{endpoint}{canonical_uri}"
        return self._client.request(method, url, params=query or None,
                                    content=payload or None, headers=request_headers)

    # -- operations ---------------------------------------------------
    def put_object(self, key: str, data: bytes,
                   content_type: str = "application/octet-stream") -> None:
        response = self._request("PUT", key, payload=data,
                                 headers={"content-type": content_type})
        if response.status_code >= 300:
            raise S3Error(f"PUT {key} -> {response.status_code}: {response.text[:300]}")

    def head_object(self, key: str) -> Optional[int]:
        """Return the object's size, or None when it does not exist."""
        response = self._request("HEAD", key)
        if response.status_code == 404:
            return None
        if response.status_code >= 300:
            raise S3Error(f"HEAD {key} -> {response.status_code}")
        return int(response.headers.get("content-length", 0))

    def bucket_exists(self) -> bool:
        response = self._request("HEAD")
        return response.status_code < 300

    def list_objects(self, prefix: str = "") -> Iterator[Tuple[str, int]]:
        """Yield ``(key, size)`` for every object under ``prefix``."""
        token: Optional[str] = None
        while True:
            query = {"list-type": "2", "max-keys": "1000"}
            if prefix:
                query["prefix"] = prefix
            if token:
                query["continuation-token"] = token
            response = self._request("GET", query=query)
            if response.status_code >= 300:
                raise S3Error(f"LIST -> {response.status_code}: {response.text[:300]}")
            body = response.text
            for match in re.finditer(
                    r"<Contents>.*?<Key>(.*?)</Key>.*?<Size>(\d+)</Size>.*?</Contents>",
                    body, re.DOTALL):
                yield match.group(1), int(match.group(2))
            truncated = "<IsTruncated>true</IsTruncated>" in body
            if not truncated:
                return
            token_match = re.search(
                r"<NextContinuationToken>(.*?)</NextContinuationToken>", body)
            if not token_match:
                return
            token = token_match.group(1)
