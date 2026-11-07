"""A short-lived signed token for the engine page, for an iframe that cannot send a bearer.

The dashboard frames the engine in a sandboxed iframe, and an iframe's
navigation carries no ``Authorization`` header. When the gateway requires
authentication, ``GET /studio`` therefore returns an ``engine_url`` with a
``t`` query parameter: ``<payload>.<signature>``, the payload base64url JSON
naming the project and the expiry, the signature HMAC-SHA256 with the
gateway's link secret (``api_gateway.files.links.link_secret``). The MAC input
is prefixed with ``imagine-engine:``, so an engine token is never a valid
signed link and a signed link is never a valid engine token.

The engine route and its assets are on the auth middleware's public list; the
route checks this token itself. The assets carry no project data. With
authentication off, the engine URL is a plain path and no token is checked.
Nothing here knows which provider authenticates callers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import structlog

logger = structlog.get_logger(__name__)

#: How long an engine URL stays usable, in seconds.
LIFETIME_S = 10 * 60
_DOMAIN = b"imagine-engine:"


class EngineTokenError(ValueError):
    """A token that is malformed, forged, expired or for another project."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(payload: str) -> bytes:
    from api_gateway.files.links import link_secret

    return hmac.new(link_secret(), _DOMAIN + payload.encode(), hashlib.sha256).digest()


def auth_required() -> bool:
    """True when the gateway asks callers for a credential."""
    from api_gateway.auth.config import load_auth_settings

    try:
        return load_auth_settings().enabled
    except Exception:  # noqa: BLE001 - a gateway that cannot read its mode refuses to start
        return True


def sign(project_id: str, lifetime_s: int = LIFETIME_S, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + lifetime_s)
    payload = _b64(json.dumps({"p": project_id, "e": exp}, separators=(",", ":")).encode())
    return f"{payload}.{_b64(_mac(payload))}"


def verify(token: str, project_id: str, now: float | None = None) -> None:
    payload, sep, mac = (token or "").partition(".")
    if not sep or not payload or not mac:
        raise EngineTokenError("not an engine token")
    try:
        given = _unb64(mac)
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise EngineTokenError("not an engine token") from exc
    if not hmac.compare_digest(_mac(payload), given):
        raise EngineTokenError("this engine token was not issued by this gateway")
    if str(data.get("p")) != project_id:
        raise EngineTokenError("this engine token is for another project")
    if int(data.get("e") or 0) < (now if now is not None else time.time()):
        raise EngineTokenError("this engine token has expired; read the studio again")
