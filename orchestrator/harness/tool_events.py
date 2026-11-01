"""Tool started/finished events for a run (FORGE-643).

A design-flow phase calls tools for minutes at a time and the dashboard sees
nothing until the phase ends. ``HarnessRuntime.call_tool`` reports each call
through the scope opened here, so whoever runs the phase decides where the
event goes: straight to the run stream in the gateway process, or over HTTP
from the Temporal worker, which has no stream of its own.

An event carries the tool name, the phase, the status and the duration. It
never carries the call's arguments or result: a stream is read by anyone who
can open the run, and arguments can hold another project's data.

Reporting is best effort. A sink that raises or stalls never changes what the
tool call does or returns.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "ToolEventScope",
    "ToolEventSink",
    "current_scope",
    "report_tool_event",
    "tool_event_scope",
]

#: Takes one event dict (see :func:`report_tool_event`) and delivers it.
ToolEventSink = Callable[[dict[str, Any]], Awaitable[None]]

#: How long leaving a scope waits for events still in flight.
FLUSH_TIMEOUT_SECONDS = 3.0


@dataclass
class ToolEventScope:
    run_id: str
    phase: str
    sink: ToolEventSink
    pending: set[asyncio.Task[None]] = field(default_factory=set)


_scope: ContextVar[ToolEventScope | None] = ContextVar("tool_event_scope", default=None)


def current_scope() -> ToolEventScope | None:
    return _scope.get()


@asynccontextmanager
async def tool_event_scope(run_id: str, phase: str, sink: ToolEventSink) -> AsyncIterator[None]:
    """Report every tool call made inside this block against ``run_id``."""
    scope = ToolEventScope(run_id=run_id, phase=phase, sink=sink)
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
        if scope.pending:
            _, still = await asyncio.wait(scope.pending, timeout=FLUSH_TIMEOUT_SECONDS)
            for task in still:
                task.cancel()


def report_tool_event(
    call_id: str,
    tool: str,
    status: str,
    *,
    duration_ms: int | None = None,
) -> None:
    """Report one call's ``started``, ``finished`` or ``failed`` edge.

    A no-op outside a :func:`tool_event_scope`. Never raises.
    """
    scope = _scope.get()
    if scope is None:
        return
    event: dict[str, Any] = {
        "call_id": call_id,
        "tool": tool,
        "phase": scope.phase,
        "status": status,
        "duration_ms": duration_ms,
    }

    async def _send() -> None:
        try:
            await scope.sink({"run_id": scope.run_id, **event})
        except Exception as exc:  # noqa: BLE001 - reporting must never break a tool call
            logger.info("tool_event_dropped", run_id=scope.run_id, tool=tool, error=str(exc))

    try:
        task = asyncio.get_running_loop().create_task(_send())
    except RuntimeError:
        return
    scope.pending.add(task)
    task.add_done_callback(scope.pending.discard)


def new_call_id() -> str:
    return uuid.uuid4().hex[:12]
