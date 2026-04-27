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

__all__ = [
    "AUTH_DENIED",
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
