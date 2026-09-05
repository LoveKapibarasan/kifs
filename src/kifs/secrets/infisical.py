"""Fetch secrets from the self-hosted Infisical ``Kifs`` project (issue #5).

Auth is universal-auth with the ``INFISICAL_KIFS_*`` machine identity kept in
``~/.env.global``. Note that ``INFISICAL_KIFS_ENDPOINT`` is stored *without* a
scheme, so ``https://`` has to be prepended before use.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Iterable, Optional

import httpx

log = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ID = "30d85d77-59ee-47af-91b5-542967ff919c"
DEFAULT_ENVIRONMENT = "prod"
DEFAULT_SECRET_PATH = "/"


def _endpoint() -> Optional[str]:
    raw = os.getenv("INFISICAL_KIFS_ENDPOINT", "").strip().strip('"')
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def _login(client: httpx.Client, base_url: str) -> Optional[str]:
    client_id = os.getenv("INFISICAL_KIFS_CLIENT_ID", "").strip().strip('"')
    client_secret = os.getenv("INFISICAL_KIFS_CLIENT_SECRET", "").strip().strip('"')
    if not client_id or not client_secret:
        return None
    response = client.post(
        f"{base_url}/api/v1/auth/universal-auth/login",
        json={"clientId": client_id, "clientSecret": client_secret},
    )
    response.raise_for_status()
    return response.json().get("accessToken")


def fetch_secrets(keys: Iterable[str]) -> Dict[str, str]:
    """Return the requested secrets. Missing keys are simply absent."""
    base_url = _endpoint()
    if not base_url:
        return {}

    workspace_id = os.getenv("INFISICAL_KIFS_WORKSPACE_ID", DEFAULT_WORKSPACE_ID)
    environment = os.getenv("INFISICAL_KIFS_ENV", DEFAULT_ENVIRONMENT)
    secret_path = os.getenv("INFISICAL_KIFS_PATH", DEFAULT_SECRET_PATH)
    wanted = set(keys)

    # Cloudflare fronts this host and rejects some default user agents, so send
    # an explicit one.
    headers = {"User-Agent": "kifs/2.0 (+https://github.com/LoveKapibarasan/kifs)"}
    with httpx.Client(timeout=15.0, headers=headers) as client:
        token = _login(client, base_url)
        if not token:
            return {}
        response = client.get(
            f"{base_url}/api/v3/secrets/raw",
            params={
                "workspaceId": workspace_id,
                "environment": environment,
                "secretPath": secret_path,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        secrets = response.json().get("secrets", [])

    found = {
        item["secretKey"]: item["secretValue"]
        for item in secrets
        if item.get("secretKey") in wanted and item.get("secretValue")
    }
    log.info(
        "Infisical: resolved %d/%d secrets from %s (env=%s, path=%s).",
        len(found), len(wanted), base_url, environment, secret_path,
    )
    return found


def push_secret(key: str, value: str) -> bool:
    """Create or update one secret. Used by ``kifs secrets push``."""
    base_url = _endpoint()
    if not base_url:
        return False

    workspace_id = os.getenv("INFISICAL_KIFS_WORKSPACE_ID", DEFAULT_WORKSPACE_ID)
    environment = os.getenv("INFISICAL_KIFS_ENV", DEFAULT_ENVIRONMENT)
    secret_path = os.getenv("INFISICAL_KIFS_PATH", DEFAULT_SECRET_PATH)
    payload = {
        "workspaceId": workspace_id,
        "environment": environment,
        "secretPath": secret_path,
        "secretValue": value,
        "type": "shared",
    }

    headers = {"User-Agent": "kifs/2.0 (+https://github.com/LoveKapibarasan/kifs)"}
    with httpx.Client(timeout=15.0, headers=headers) as client:
        token = _login(client, base_url)
        if not token:
            return False
        auth = {"Authorization": f"Bearer {token}"}
        # POST creates; if the secret already exists Infisical answers 400/409,
        # in which case PATCH updates it in place.
        response = client.post(f"{base_url}/api/v3/secrets/raw/{key}", json=payload, headers=auth)
        if response.status_code in (400, 409):
            response = client.patch(f"{base_url}/api/v3/secrets/raw/{key}", json=payload, headers=auth)
        return response.status_code < 300
