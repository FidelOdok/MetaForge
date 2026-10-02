"""The design-flow worker as an authenticated service caller (FORGE-487).

A server-driven design-flow run makes its tool calls from the
``design-flow-worker`` process, over HTTP, to the MCP sidecar. The sidecar
classified every such call ``untrusted`` and held each write for a dashboard
click, so no phase could record anything unattended.

The human already authorised the run: they approved the flow version and they
answer each phase gate. What is missing is a way for the sidecar to tell that
caller apart from anyone else holding the sidecar's ordinary API key, a
plugin client for instance. This module is that, and it is built so that the
cheap shortcuts do not work:

* **A dedicated secret.** ``METAFORGE_MCP_SERVICE_KEY`` is separate from the
  bearer key (``METAFORGE_MCP_API_KEY`` / ``METAFORGE_MCP_CLIENT_KEY``). The
  bearer key is handed to every plugin client, so reusing it would promote all
  of them. The service key is shared by exactly two services.
* **Never network position.** Nothing here looks at an address. The key is the
  only thing that makes a caller a service.
* **Off unless configured.** No key on the sidecar, or one too short to
  resist guessing, means the feature is off and the worker stays untrusted.
  There is no default value, and open auth mode does not enable it.
* **Constant-time compare**, on bytes, so a non-ASCII header cannot raise.
* **A key is not a grant.** A valid key only says *who*. What the call may do
  is bound to a run the gateway confirms is running an approved flow version,
  and to that run's project (:class:`ServiceRunVerifier`).

Layer-1 module: stdlib only, no env reads, no side effects on import.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "HEADER_SERVICE_KEY",
    "MIN_SERVICE_KEY_LENGTH",
    "ServiceGrant",
    "ServiceRunVerifier",
    "ServiceScopeError",
    "service_key_is_usable",
    "verify_service_key",
]

#: Carries the service credential. Its own header, not ``Authorization``: that
#: one holds the bearer key the sidecar's ordinary auth consumes.
HEADER_SERVICE_KEY = "X-MetaForge-Service-Key"

#: Below this a configured key is treated as no key. A one-word secret is a
#: guessable one, and "configured" must not be enough to switch on a bypass of
#: per-call approval.
MIN_SERVICE_KEY_LENGTH = 16


def service_key_is_usable(configured: str | None) -> bool:
    """Whether a configured key is strong enough to enable the feature."""
    return bool(configured) and len(configured or "") >= MIN_SERVICE_KEY_LENGTH


def verify_service_key(provided: str | None, configured: str | None) -> bool:
    """Constant-time match of a presented key against the configured one.

    False whenever the feature is off (``configured`` empty or too short), the
    caller sent nothing, or the keys differ. There is deliberately no "open
    mode" branch: unlike ``mcp_core.auth.verify_api_key``, an unset secret
    means *nobody* is a service.
    """
    if not service_key_is_usable(configured) or not provided:
        return False
    assert configured is not None  # narrowed by service_key_is_usable
    return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))


@dataclass(frozen=True)
class ServiceGrant:
    """What the gateway confirmed about a run, for one verified call."""

    run_id: str
    project_id: str
    flow_version_id: str


class ServiceScopeError(RuntimeError):
    """A service call named a run or project it is not entitled to.

    Also raised for a call the service caller may never make (destructive or
    admin tools): either way the call is refused, not held, because a hold has
    no human waiting on the other end of a server-driven run.
    """

    def __init__(self, tool_id: str, reason: str, *, code: str = "service_scope") -> None:
        self.tool_id = tool_id
        self.reason = reason
        self.code = code
        super().__init__(
            f"{tool_id or 'call'} was refused for the design-flow service caller: {reason}"
        )


class ServiceRunVerifier(Protocol):
    """Asks the authority on runs whether a service call is in scope.

    Implemented over HTTP against the gateway, which owns run state and flow
    versions. ``mcp_core`` takes it injected, the same way it takes the
    approval gate, and does not import upward.
    """

    async def verify(self, run_id: str, project_id: str) -> ServiceGrant | None:
        """The grant, or ``None`` when the run is a real, running run that has
        no approved flow version behind it (a built-in template run): there is
        nothing to bind a service grant to, so the caller stays untrusted.

        Raises :class:`ServiceScopeError` when the run is not running, does not
        exist, or belongs to another project, and any other exception when the
        authority could not be asked (the call fails closed).
        """
        ...
