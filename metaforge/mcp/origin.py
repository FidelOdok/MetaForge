"""Which browser origins may call the MCP endpoint (FORGE-604).

The Streamable HTTP transport requires it (MUST since 2025-03-26, with the
403 spelled out in 2025-11-25): a server validates the ``Origin`` header on
every request and answers an invalid one with ``403 Forbidden``. The threat
is DNS rebinding. A page on ``evil.example`` re-points its own name at the
machine running this server, and the browser then lets that page post to
it, carrying ``Origin: http://evil.example``.

Matching ``Origin`` against the request's ``Host`` header is the obvious
check and the wrong one: after a rebinding the two agree, because the
attacker controls both. So the check is an explicit allow-list:

* **No ``Origin``**: allowed. Only browsers send it; Claude Code, Codex, the
  design-flow worker and other servers do not, and nothing about a missing
  header lets a web page in.
* **Loopback** (``localhost``, ``*.localhost``, ``127.0.0.0/8``, ``[::1]``,
  any port): allowed. A rebinding page has its own name, never these.
* **This server's public origin** when ``METAFORGE_OAUTH_ISSUER`` names one
  (the OAuth pages it serves post back to it).
* **The hosted Claude web app** (``https://claude.ai``, ``https://claude.com``),
  whose connector reaches this server.
* **``METAFORGE_MCP_ALLOWED_ORIGINS``**: a comma-separated list of extra
  origins, for a deployment with its own browser front end. ``*`` turns the
  check off, and start-up logs that it is off.

Everything else, ``Origin: null`` included (sandboxed frames and files),
is refused.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "ALLOWED_ORIGINS_ENV",
    "DEFAULT_TRUSTED_ORIGINS",
    "OriginPolicy",
    "normalise_origin",
]

ALLOWED_ORIGINS_ENV = "METAFORGE_MCP_ALLOWED_ORIGINS"

#: Browser front ends that call this server as a matter of course.
DEFAULT_TRUSTED_ORIGINS: tuple[str, ...] = ("https://claude.ai", "https://claude.com")

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalise_origin(value: str) -> str | None:
    """``scheme://host[:port]`` in lower case with a default port dropped.

    ``None`` for anything that is not a plain http(s) origin, including the
    literal ``null`` browsers send for opaque origins.
    """
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _is_loopback(origin: str) -> bool:
    host = (urlsplit(origin).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class OriginPolicy:
    """The allow-list, built once at start-up."""

    allowed: frozenset[str] = field(default_factory=frozenset)
    allow_any: bool = False

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, issuer: str | None = None
    ) -> OriginPolicy:
        src = os.environ if env is None else env
        raw = [p.strip() for p in str(src.get(ALLOWED_ORIGINS_ENV) or "").split(",")]
        if "*" in raw:
            logger.warning(
                "mcp_origin_check_disabled",
                setting=ALLOWED_ORIGINS_ENV,
                consequence="any web page that can reach this server can call it",
            )
            return cls(allow_any=True)
        allowed: set[str] = set()
        for candidate in [*DEFAULT_TRUSTED_ORIGINS, *(issuer and [issuer] or []), *raw]:
            if not candidate:
                continue
            origin = normalise_origin(candidate)
            if origin is None:
                logger.warning("mcp_origin_setting_ignored", value=candidate[:200])
                continue
            allowed.add(origin)
        return cls(allowed=frozenset(allowed))

    def allows(self, origin_header: str | None) -> bool:
        """Whether a request carrying this ``Origin`` may proceed."""
        if origin_header is None or not origin_header.strip():
            return True
        if self.allow_any:
            return True
        origin = normalise_origin(origin_header)
        if origin is None:
            return False
        return _is_loopback(origin) or origin in self.allowed
