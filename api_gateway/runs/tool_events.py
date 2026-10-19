"""Getting a phase's tool events onto the run stream (FORGE-643).

``HarnessRuntime.call_tool`` reports each call through the scope that
:func:`orchestrator.harness.tool_events.tool_event_scope` opens. This module
chooses where those events go:

* inside the gateway, straight into the run stream;
* in the design-flow worker, which has no stream, over HTTP to the gateway.

The events carry no arguments and no results.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from api_gateway.runs.gate_announce import gateway_url
from orchestrator.harness.tool_events import ToolEventSink, tool_event_scope

logger = structlog.get_logger(__name__)

__all__ = ["ScopedPhaseBrain", "http_tool_event_sink", "local_tool_event_sink"]

_TIMEOUT_SECONDS = 5.0


def local_tool_event_sink() -> ToolEventSink:
    """A sink for a process that owns the run stream (the gateway)."""

    async def sink(event: dict[str, Any]) -> None:
        from api_gateway.runs.routes import publish_tool_event

        publish_tool_event(
            str(event["run_id"]),
            call_id=str(event["call_id"]),
            tool=str(event["tool"]),
            phase=str(event.get("phase") or ""),
            status=str(event["status"]),
            duration_ms=event.get("duration_ms"),
        )

    return sink


def http_tool_event_sink(client: httpx.AsyncClient | None = None) -> ToolEventSink:
    """A sink for a process with no run stream: post to the gateway.

    Same address and key as the gate announcer. A non-2xx answer raises, and
    the scope drops the event: progress is never worth failing a tool call for.
    """

    async def sink(event: dict[str, Any]) -> None:
        headers = {}
        key = (os.environ.get("METAFORGE_GATEWAY_API_KEY") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = f"{gateway_url()}/v1/runs/{event['run_id']}/tool-events"
        body = {k: v for k, v in event.items() if k != "run_id"}
        if client is not None:
            resp = await client.post(url, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.post(url, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()

    return sink


class ScopedPhaseBrain:
    """A phase brain whose tool calls are reported against the run."""

    def __init__(self, brain: Any, run_id: str, sink: ToolEventSink) -> None:
        self._brain = brain
        self._run_id = run_id
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        return getattr(self._brain, name)

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> Any:
        async with tool_event_scope(self._run_id, str(phase.id), self._sink):
            return await self._brain.run_phase(goal=goal, phase=phase, context=context)
