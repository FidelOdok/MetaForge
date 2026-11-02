"""Signed links: a URL that is its own credential, for a short while.

A link lets a person, a browser or ``curl`` fetch a work product's file, or
send files into an upload, without the gateway's bearer token. That is what
makes files reachable from every client: a chat-only client can show a link,
and Claude Code or Codex can ``curl`` one, while the plugin's OAuth token
stays where it is.

A token is ``<payload>.<signature>``: the payload is base64url JSON naming
what the link is for (``kind``, ``target``, ``path``) and when it ends
(``exp``); the signature is HMAC-SHA256 over the payload with the gateway's
link secret. Nothing is stored per link, so a link cannot be listed or
revoked one by one; it ends at ``exp``, and changing the secret ends them all.

The secret is ``METAFORGE_LINK_SECRET``, else a random one kept in
``~/.metaforge/link_secret`` (``METAFORGE_LINK_SECRET_PATH``) so a restart does
not break links already handed out.

Lifetimes grow with the file: a large upload on a slow connection needs
longer than a small download (:func:`lifetime_for`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

LinkKind = Literal["download", "upload"]

#: Shortest and longest a link lives, in seconds.
MIN_LIFETIME_S = 15 * 60
MAX_LIFETIME_S = 24 * 60 * 60
#: Time allowed per megabyte on top of the minimum: about 0.4 Mbit/s, a poor
#: mobile connection, so a link outlives the transfer it was made for.
SECONDS_PER_MB = 20

_secret_cache: bytes | None = None


class LinkError(ValueError):
    """A token that is malformed, forged or expired."""


@dataclass(frozen=True)
class Link:
    kind: LinkKind
    target: str
    path: str
    exp: int

    @property
    def expires_at(self) -> str:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(self.exp, UTC).isoformat()


def lifetime_for(size_bytes: int, requested_s: int | None = None) -> int:
    """Seconds a link for ``size_bytes`` lives: asked for, or by size, within the bounds."""
    by_size = MIN_LIFETIME_S + SECONDS_PER_MB * (max(size_bytes, 0) // (1024 * 1024))
    wanted = requested_s if requested_s is not None else by_size
    return max(60, min(int(wanted), MAX_LIFETIME_S))


def _secret_path() -> Path:
    override = os.environ.get("METAFORGE_LINK_SECRET_PATH", "").strip()
    return Path(override) if override else Path.home() / ".metaforge" / "link_secret"


def link_secret() -> bytes:
    """The signing secret: the env, else the kept file, else a new kept one."""
    global _secret_cache  # noqa: PLW0603
    env = os.environ.get("METAFORGE_LINK_SECRET", "").strip()
    if env:
        return env.encode()
    if _secret_cache is not None:
        return _secret_cache
    path = _secret_path()
    try:
        value = path.read_bytes().strip()
    except OSError:
        value = b""
    if len(value) < 32:
        value = secrets.token_hex(32).encode()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            path.chmod(0o600)
        except OSError as exc:
            # Links still work until a restart; say so rather than fail.
            logger.warning("link_secret_not_saved", path=str(path), error=str(exc))
    _secret_cache = value
    return value


def reset_secret_cache() -> None:
    """Forget the cached secret (tests that change it)."""
    global _secret_cache  # noqa: PLW0603
    _secret_cache = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(kind: LinkKind, target: str, *, path: str = "", lifetime_s: int) -> tuple[str, Link]:
    """A token for ``kind`` on ``target`` (a work product or upload id)."""
    link = Link(kind=kind, target=target, path=path, exp=int(time.time()) + lifetime_s)
    payload = _b64(
        json.dumps(
            {"k": link.kind, "t": link.target, "p": link.path, "e": link.exp},
            separators=(",", ":"),
        ).encode()
    )
    mac = hmac.new(link_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}", link


def verify(token: str, kind: LinkKind | None = None) -> Link:
    """The link a token stands for, if it is genuine, unexpired and (given) of ``kind``."""
    payload, sep, mac = token.partition(".")
    if not sep or not payload or not mac:
        raise LinkError("not a MetaForge link")
    expected = hmac.new(link_secret(), payload.encode(), hashlib.sha256).digest()
    try:
        given = _unb64(mac)
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise LinkError("not a MetaForge link") from exc
    if not hmac.compare_digest(expected, given):
        raise LinkError("this link was not issued by this gateway")
    link = Link(kind=data["k"], target=data["t"], path=data.get("p", ""), exp=int(data["e"]))
    if kind is not None and link.kind != kind:
        raise LinkError(f"this link is for {link.kind}, not {kind}")
    if link.exp < time.time():
        raise LinkError("this link has expired; ask for a new one")
    return link


def object_store_link(object_key: str, lifetime_s: int) -> str | None:
    """A presigned object-store URL, when the store has a public address.

    ``METAFORGE_LINK_OBJECT_STORE_ENDPOINT`` is the store's address as a
    person's browser reaches it (the in-cluster ``minio:9000`` is not). A
    presigned URL is signed for one host, so it is built on that address.
    Unset, every link goes through the gateway.
    """
    endpoint = os.environ.get("METAFORGE_LINK_OBJECT_STORE_ENDPOINT", "").strip()
    if not endpoint:
        return None
    from datetime import timedelta

    from digital_twin.storage.work_product_blobs import presign_work_product_blob

    try:
        return presign_work_product_blob(
            object_key, timedelta(seconds=lifetime_s), public_endpoint=endpoint
        )
    except Exception as exc:  # noqa: BLE001 - fall back to a gateway link
        logger.warning("object_store_link_failed", key=object_key, error=str(exc))
        return None
