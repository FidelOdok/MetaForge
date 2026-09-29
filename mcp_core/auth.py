"""API-key auth for MCP transports (MET-338).

Single comparison helper that the entrypoint (server-side) and the
gateway (client-side) both use, so the wire-format and timing-safe
compare logic live in exactly one place.

Defaults to **open mode** when no key is configured. Production
deployments set ``METAFORGE_MCP_API_KEY`` on the server side and
``METAFORGE_MCP_CLIENT_KEY`` on the client side; the values must match
under constant-time compare.

This module deliberately has zero side effects on import per
``mcp_core``'s CLAUDE.md (no logging at module load, no env reads at
import time).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AUTH_DENIED",
    "AuthPosture",
    "AuthResult",
    "redact",
    "verify_api_key",
]


AUTH_DENIED = "auth_error"


class AuthResult:
    """Outcome of a single auth check.

    Tiny class so callers can short-circuit on ``ok`` without
    inspecting the redacted hint themselves. Attribute access keeps
    the call sites tidy: ``if not result.ok: ...``.
    """

    __slots__ = ("ok", "redacted", "reason")

    def __init__(self, ok: bool, *, redacted: str = "", reason: str = "") -> None:
        self.ok = ok
        self.redacted = redacted
        self.reason = reason

    def __bool__(self) -> bool:
        return self.ok


def verify_api_key(provided: str | None, expected: str | None) -> AuthResult:
    """Constant-time compare ``provided`` against ``expected``.

    Semantics:

    * ``expected`` is ``None`` or empty → **open mode**, every
      connection passes (``ok=True``). Caller must not enforce.
    * ``expected`` is set, ``provided`` is missing or empty → reject.
    * Both set → ``hmac.compare_digest`` constant-time compare.
    """
    if not expected:
        return AuthResult(True, reason="open_mode")
    if not provided:
        return AuthResult(False, reason="missing_key")
    if hmac.compare_digest(provided, expected):
        return AuthResult(True, redacted=redact(provided), reason="match")
    return AuthResult(False, redacted=redact(provided), reason="mismatch")


def redact(key: str, *, keep: int = 4) -> str:
    """Return ``********<last keep chars>`` for safe logging.

    Never log the full key. The redacted form is enough to
    correlate connection events with rotation logs without leaking
    the secret.
    """
    if not key:
        return ""
    if len(key) <= keep:
        return "*" * len(key)
    return "*" * 8 + key[-keep:]


@dataclass(frozen=True, slots=True)
class AuthPosture:
    """What the transport in front of the MCP server actually enforces.

    FORGE-332. The server object cannot work this out for itself: auth is
    decided by whatever terminates the connection (the stdio launcher, the
    HTTP app), and by the time a request reaches dispatch the decision has
    already been made and thrown away. So the transport declares it, once,
    at construction — and ``health/check`` can then answer "what is
    protecting this connection?" instead of leaving the caller to guess.

    Open mode is the default in both transports and is easy to arrive at by
    accident: it is what you get from an unset environment variable. A
    health report that stays silent about it is the same failure as one
    that says "healthy" with every adapter down.

    Deliberately not an env reader — ``mcp_core`` has no import-time side
    effects (see this module's docstring). The transport passes the values.
    """

    api_key: bool = False
    oauth: bool = False
    transport: str = "unknown"

    @property
    def mode(self) -> str:
        if self.api_key and self.oauth:
            return "api_key+oauth"
        if self.oauth:
            return "oauth"
        if self.api_key:
            return "api_key"
        return "open"

    @property
    def is_open(self) -> bool:
        """True when nothing is configured and every connection passes."""
        return not (self.api_key or self.oauth)

    def report(self) -> dict[str, Any]:
        """The ``auth`` block of a health report."""
        out: dict[str, Any] = {
            "mode": self.mode,
            "transport": self.transport,
            # A shared API key authorises the call but identifies nobody —
            # the same distinction the HTTP gate already draws by returning
            # None for a key match and an actor for an OAuth token. Only
            # OAuth produces an attributable caller.
            "identifies_caller": self.oauth,
        }
        if self.is_open:
            out["detail"] = (
                "no API key and no OAuth configured: every connection on this "
                "transport is accepted, and no call is attributable to anyone"
            )
        return out


UNKNOWN_AUTH: dict[str, Any] = {
    "mode": "unknown",
    "detail": (
        "the transport did not declare an auth posture; this server cannot say "
        "what is protecting it. Assume nothing."
    ),
}
