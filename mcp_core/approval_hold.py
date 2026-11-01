"""Telling an MCP client its call is held, and for how long (FORGE-465).

A write held for the dashboard used to be silent until it ended. The client
saw nothing while a person was (or was not) looking at the Approvals page,
and the hold window (180 s) outlived the client's own tool timeout (Claude
Code: 120 s). So the client gave up first, the agent read "tool timed out
after 120s", and told the user MetaForge had never asked for anything, while
the approval sat in the queue with nobody waiting on it.

Two things fix that, and this module holds the parts that do not depend on a
transport:

* **An early signal.** When the client sent a ``progressToken`` and the
  transport can deliver a notification for this call, the server sends
  ``notifications/progress`` naming the approval id and where to answer it,
  then repeats it while the call waits. A client that resets its timeout on
  progress then waits for the person instead of giving up on them.
* **A window that fits the client.** Without a progress channel nothing
  resets the client's timeout, so the hold must end before it does. The
  default for that case is below common client tool timeouts; with progress
  the longer window is kept. Both are configurable.

``mcp_core`` does not import upward, so the transport supplies delivery
through :class:`HoldNotifier` and the server decides everything else.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_HOLD_SECONDS",
    "DEFAULT_HOLD_WITH_PROGRESS_SECONDS",
    "DEFAULT_PROGRESS_INTERVAL_SECONDS",
    "ENV_HOLD_SECONDS",
    "ENV_HOLD_WITH_PROGRESS_SECONDS",
    "ENV_PROGRESS_INTERVAL_SECONDS",
    "HoldNotifier",
    "hold_notice_text",
    "hold_window_seconds",
    "progress_interval_seconds",
    "progress_notification",
]

#: Window for a held call whose client cannot be sent progress.
ENV_HOLD_SECONDS = "METAFORGE_APPROVAL_HOLD_SECONDS"
#: Window for a held call whose client is being sent progress.
ENV_HOLD_WITH_PROGRESS_SECONDS = "METAFORGE_APPROVAL_HOLD_PROGRESS_SECONDS"
#: How often a held call repeats its progress notification.
ENV_PROGRESS_INTERVAL_SECONDS = "METAFORGE_APPROVAL_PROGRESS_INTERVAL_SECONDS"

#: Below Claude Code's 120 s tool timeout, with room for the request itself.
#: Nothing resets that timeout without progress, so a longer hold ends with
#: the client gone and the agent reading a bare timeout.
DEFAULT_HOLD_SECONDS = 100.0
#: The window the gates had before FORGE-465. Kept when progress is sent,
#: because then the client's timeout is not the limit.
DEFAULT_HOLD_WITH_PROGRESS_SECONDS = 180.0
#: Well inside any client timeout, and rare enough not to flood a stream.
DEFAULT_PROGRESS_INTERVAL_SECONDS = 10.0

#: Matches the gateway's cap on a requested window.
_MAX_WINDOW_SECONDS = 3600.0


class HoldNotifier(Protocol):
    """The transport's way to send one notification for the current call.

    ``available`` says whether a notification sent now would reach the
    client of the call being handled. ``send`` delivers one and returns
    whether it did. Both are answered per call, because one HTTP server
    serves many clients and only some of their calls carry a stream.
    """

    def available(self) -> bool: ...

    def send(self, message: dict[str, Any]) -> bool: ...


def _seconds_from_env(name: str, default: float, environ: Mapping[str, str] | None) -> float:
    env = os.environ if environ is None else environ
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not 0 < value <= _MAX_WINDOW_SECONDS:
        # Loud on purpose: a typo here silently changing how long writes
        # wait is the kind of fallback nobody notices until it matters.
        logger.warning("approval_hold_env_invalid", variable=name, value=raw[:32], using=default)
        return default
    return value


def hold_window_seconds(*, progress: bool, environ: Mapping[str, str] | None = None) -> float:
    """How long a held call waits, given whether its client gets progress."""
    if progress:
        return _seconds_from_env(
            ENV_HOLD_WITH_PROGRESS_SECONDS, DEFAULT_HOLD_WITH_PROGRESS_SECONDS, environ
        )
    return _seconds_from_env(ENV_HOLD_SECONDS, DEFAULT_HOLD_SECONDS, environ)


def progress_interval_seconds(environ: Mapping[str, str] | None = None) -> float:
    """How often a held call repeats its progress notification."""
    return _seconds_from_env(
        ENV_PROGRESS_INTERVAL_SECONDS, DEFAULT_PROGRESS_INTERVAL_SECONDS, environ
    )


def hold_notice_text(
    tool_id: str, approval_id: str | None, approvals_url: str | None, window_seconds: float
) -> str:
    """The sentence a client shows, and an agent reads, while a call is held."""
    ident = f" (approval {approval_id})" if approval_id else ""
    where = (
        f"the MetaForge dashboard Approvals page: {approvals_url}"
        if approvals_url
        else "the MetaForge dashboard Approvals page"
    )
    return (
        f"{tool_id} is held for human approval{ident}. A person must approve or "
        f"reject it on {where}. Waiting up to {window_seconds:.0f}s."
    )


def progress_notification(
    token: str | int, *, progress: float, total: float, message: str
) -> dict[str, Any]:
    """A ``notifications/progress`` message for one held call."""
    return {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {
            "progressToken": token,
            "progress": progress,
            "total": total,
            "message": message,
        },
    }
