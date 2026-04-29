"""Streaming progress for long-running MCP tools (MET-388).

Tool handlers signal progress with ``await emit_progress(progress, message)``.
The server populates a contextvar ``_active_emitter`` for the duration of a
request; outside that scope the emit is a no-op so handlers can be written
without branching on whether progress is being captured.

Wire encoding: each progress event is a JSON-RPC 2.0 notification
``{"jsonrpc": "2.0", "method": "notifications/progress", "params": {...}}``
where ``params`` is the dump of ``mcp_core.schemas.ToolProgress``. Transports
choose how to deliver them (SSE frames, stdio lines, in-memory queue).

Layer-1 invariant: stdlib + pydantic only. The server (layer 3) supplies
the sink; this module just routes events to whichever sink is active.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token

from mcp_core.schemas import ToolProgress

ProgressEmitter = Callable[[ToolProgress], Awaitable[None]]

_active_emitter: ContextVar[ProgressEmitter | None] = ContextVar(
    "mcp_progress_emitter", default=None
)


def set_emitter(emitter: ProgressEmitter | None) -> Token[ProgressEmitter | None]:
    """Install ``emitter`` for the current async context.

    Returns a Token the caller passes to :func:`reset_emitter` so progress
    routing is scoped per-request without leaking between concurrent calls.
    """
    return _active_emitter.set(emitter)


def reset_emitter(token: Token[ProgressEmitter | None]) -> None:
    """Restore the emitter to its prior value."""
    _active_emitter.reset(token)


def current_emitter() -> ProgressEmitter | None:
    """Return the active emitter or ``None`` outside a request."""
    return _active_emitter.get()


async def emit_progress(
    *,
    request_id: str,
    progress: float,
    message: str = "",
) -> bool:
    """Send a progress event if an emitter is installed.

    Returns ``True`` when the emitter accepted the event, ``False`` when no
    emitter was active (handler ran outside a streaming context).
    """
    emitter = _active_emitter.get()
    if emitter is None:
        return False
    event = ToolProgress(
        request_id=request_id,
        progress=progress,
        message=message,
    )
    await emitter(event)
    return True


def progress_notification(event: ToolProgress) -> dict[str, object]:
    """Encode a progress event as a JSON-RPC 2.0 notification dict.

    Notifications have no ``id``; they're a server→client signal that
    dies if the client doesn't read them. Transports serialise the
    return value to JSON.
    """
    return {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": event.model_dump(),
    }


__all__ = [
    "ProgressEmitter",
    "current_emitter",
    "emit_progress",
    "progress_notification",
    "reset_emitter",
    "set_emitter",
]
