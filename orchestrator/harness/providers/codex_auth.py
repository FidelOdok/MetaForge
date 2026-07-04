"""ChatGPT-subscription (Codex OAuth) credential handling (MET-550).

Reuses the official Codex CLI login rather than reimplementing it: the user
runs ``npx @openai/codex login`` once, which writes ``~/.codex/auth.json``.
This module reads those credentials, decodes token claims, detects expiry, and
refreshes the OAuth token — so a ChatGPT Plus/Pro subscription can pay for
model calls with no API key (the ``openai-codex`` provider).

The token refresh HTTP call is injectable, so this is unit-tested without
network. Volatile constants (client id, token URL, backend) live here.

CAVEAT: the backend is undocumented and can change without notice.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Reference constants (from the official codex CLI / community auth plugins).
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_BACKEND_BASE = "https://chatgpt.com/backend-api/codex"

# Injected refresh transport: (url, json_body) -> parsed json response.
RefreshPost = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class CodexAuthError(RuntimeError):
    """Codex credentials are missing or unusable."""


@dataclass
class CodexCredentials:
    access_token: str
    refresh_token: str | None = None
    account_id: str | None = None
    id_token: str | None = None
    expires_at: float | None = None  # epoch seconds

    def is_expired(self, *, now: float | None = None, skew: float = 60.0) -> bool:
        if self.expires_at is None:
            return False
        return (now or time.time()) >= self.expires_at - skew


def auth_json_path() -> Path | None:
    """First existing Codex auth.json across the known locations."""
    candidates: list[Path] = []
    for env in ("CODEX_HOME", "CHATGPT_LOCAL_HOME"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "auth.json")
    candidates.append(Path.home() / ".codex" / "auth.json")
    candidates.append(Path.home() / ".chatgpt-local" / "auth.json")
    return next((p for p in candidates if p.is_file()), None)


def _decode_jwt_payload(token: str | None) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
        decoded = json.loads(raw)
    except (binascii.Error, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _account_id_from_id_token(id_token: str | None) -> str | None:
    claims = _decode_jwt_payload(id_token)
    if not claims:
        return None
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        acct = auth_claim.get("chatgpt_account_id") or auth_claim.get("account_id")
        if acct:
            return str(acct)
    acct = claims.get("chatgpt_account_id") or claims.get("account_id")
    return str(acct) if acct else None


def _token_exp(token: str | None) -> float | None:
    exp = _decode_jwt_payload(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def parse_credentials(data: dict[str, Any]) -> CodexCredentials:
    """Parse an auth.json dict, tolerant of field placement across versions."""
    raw_tokens = data.get("tokens")
    tokens: dict[str, Any] = raw_tokens if isinstance(raw_tokens, dict) else {}
    access_token = tokens.get("access_token") or data.get("access_token")
    if not access_token:
        raise CodexAuthError("no access_token in Codex auth.json")
    id_token = tokens.get("id_token") or data.get("id_token")
    account_id = (
        tokens.get("account_id") or data.get("account_id") or _account_id_from_id_token(id_token)
    )
    return CodexCredentials(
        access_token=str(access_token),
        refresh_token=tokens.get("refresh_token") or data.get("refresh_token"),
        account_id=str(account_id) if account_id else None,
        id_token=id_token,
        expires_at=_token_exp(access_token),
    )


def load_credentials(path: Path | None = None) -> CodexCredentials:
    resolved = path or auth_json_path()
    if resolved is None or not resolved.is_file():
        raise CodexAuthError("no Codex credentials found — run `npx @openai/codex login` first")
    return parse_credentials(json.loads(resolved.read_text(encoding="utf-8")))


async def refresh_credentials(
    creds: CodexCredentials,
    *,
    post: RefreshPost,
    now: float | None = None,
) -> CodexCredentials:
    """Refresh via the OAuth token endpoint. Returns new credentials."""
    if not creds.refresh_token:
        raise CodexAuthError("cannot refresh: no refresh_token")
    # Form-encoded by the transport; no scope on refresh (matches the codex CLI).
    body = {
        "client_id": CODEX_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": creds.refresh_token,
    }
    resp = await post(CODEX_TOKEN_URL, body)
    access_token = resp.get("access_token")
    if not access_token:
        raise CodexAuthError("token refresh returned no access_token")
    id_token = resp.get("id_token") or creds.id_token
    expires_in = resp.get("expires_in")
    expires_at = _token_exp(access_token)
    if expires_at is None and isinstance(expires_in, (int, float)):
        expires_at = (now or time.time()) + float(expires_in)
    logger.info("codex_token_refreshed")
    return CodexCredentials(
        access_token=str(access_token),
        refresh_token=resp.get("refresh_token") or creds.refresh_token,
        account_id=creds.account_id or _account_id_from_id_token(id_token),
        id_token=id_token,
        expires_at=expires_at,
    )


async def get_valid_credentials(
    *,
    path: Path | None = None,
    post: RefreshPost | None = None,
    now: float | None = None,
) -> CodexCredentials:
    """Load creds and refresh them if expired (when a refresh transport is given)."""
    creds = load_credentials(path)
    if creds.is_expired(now=now) and creds.refresh_token and post is not None:
        creds = await refresh_credentials(creds, post=post, now=now)
    return creds
