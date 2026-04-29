"""Unit tests for MCP streaming progress (MET-388)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.progress import (
    current_emitter,
    emit_progress,
    progress_notification,
    reset_emitter,
    set_emitter,
)
from mcp_core.schemas import ToolProgress
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _CaptureSink:
    """Records every ToolProgress sent through it. Async-callable."""

    def __init__(self) -> None:
        self.events: list[ToolProgress] = []

    async def __call__(self, event: ToolProgress) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# emit_progress / set_emitter / reset_emitter
# ---------------------------------------------------------------------------


class TestEmitter:
    async def test_no_emitter_returns_false(self) -> None:
        assert current_emitter() is None
        result = await emit_progress(request_id="req-1", progress=0.5, message="x")
        assert result is False

    async def test_set_emitter_routes_events(self) -> None:
        sink = _CaptureSink()
        token = set_emitter(sink)
        try:
            assert current_emitter() is sink
            ok = await emit_progress(request_id="req-1", progress=0.25, message="working")
            assert ok is True
        finally:
            reset_emitter(token)

        assert len(sink.events) == 1
        ev = sink.events[0]
        assert ev.request_id == "req-1"
        assert ev.progress == 0.25
        assert ev.message == "working"

    async def test_reset_restores_prior_emitter(self) -> None:
        outer = _CaptureSink()
        inner = _CaptureSink()
        outer_token = set_emitter(outer)
        try:
            inner_token = set_emitter(inner)
            try:
                await emit_progress(request_id="r", progress=0.1)
            finally:
                reset_emitter(inner_token)
            await emit_progress(request_id="r", progress=0.2)
        finally:
            reset_emitter(outer_token)
        assert [e.progress for e in outer.events] == [0.2]
        assert [e.progress for e in inner.events] == [0.1]

    async def test_concurrent_emitters_isolated_per_task(self) -> None:
        """ContextVar copy_context inheritance keeps progress per-task."""

        sink_a = _CaptureSink()
        sink_b = _CaptureSink()

        async def worker(sink: _CaptureSink, label: str) -> None:
            token = set_emitter(sink)
            try:
                await asyncio.sleep(0)  # let the other task race
                await emit_progress(request_id=label, progress=0.5, message=label)
            finally:
                reset_emitter(token)

        await asyncio.gather(worker(sink_a, "A"), worker(sink_b, "B"))
        assert len(sink_a.events) == 1 and sink_a.events[0].request_id == "A"
        assert len(sink_b.events) == 1 and sink_b.events[0].request_id == "B"


# ---------------------------------------------------------------------------
# Wire encoding
# ---------------------------------------------------------------------------


class TestProgressNotification:
    def test_encodes_as_jsonrpc_notification(self) -> None:
        ev = ToolProgress(request_id="r", progress=0.4, message="halfway")
        notif = progress_notification(ev)
        assert notif["jsonrpc"] == "2.0"
        assert notif["method"] == "notifications/progress"
        # Notifications must NOT carry an id.
        assert "id" not in notif
        params = notif["params"]
        assert isinstance(params, dict)
        assert params["request_id"] == "r"
        assert params["progress"] == 0.4
        assert params["message"] == "halfway"

    def test_can_serialise_to_json(self) -> None:
        ev = ToolProgress(request_id="r", progress=1.0)
        encoded = json.dumps(progress_notification(ev))
        assert "notifications/progress" in encoded


# ---------------------------------------------------------------------------
# Server integration — sink installed only for tool/call, scoped per request
# ---------------------------------------------------------------------------


class _SlowServer(McpToolServer):
    """Adapter whose only tool emits two progress events before returning."""

    def __init__(self) -> None:
        super().__init__(adapter_id="slow", version="0.1.0")
        manifest = ToolManifest(
            tool_id="slow.run",
            adapter_id="slow",
            name="Run a slow job",
            description="emits 2 progress events",
            capability="cad.demo",
        )
        self.register_tool(manifest=manifest, handler=self._run)

    async def _run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        await emit_progress(request_id="job-1", progress=0.33, message="step 1")
        await emit_progress(request_id="job-1", progress=0.66, message="step 2")
        return {"echo": arguments}


def _request(method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": "1", "method": method, "params": params})


class TestServerWiring:
    async def test_no_sink_handler_runs_without_streaming(self) -> None:
        srv = _SlowServer()
        # No sink installed — emit_progress returns False but handler still
        # completes and the final response is well-formed.
        raw = await srv.handle_request(
            _request("tool/call", {"tool_id": "slow.run", "arguments": {"x": 1}})
        )
        body = json.loads(raw)
        assert body["result"]["status"] == "success"
        assert body["result"]["data"] == {"echo": {"x": 1}}

    async def test_sink_captures_progress_events_in_order(self) -> None:
        srv = _SlowServer()
        sink = _CaptureSink()
        srv.set_progress_sink(sink)

        raw = await srv.handle_request(
            _request("tool/call", {"tool_id": "slow.run", "arguments": {}})
        )
        body = json.loads(raw)
        assert body["result"]["status"] == "success"

        progresses = [e.progress for e in sink.events]
        assert progresses == [0.33, 0.66]
        assert [e.message for e in sink.events] == ["step 1", "step 2"]

    async def test_sink_scoped_to_tool_call_only(self) -> None:
        """Scope check: nothing happens during tool/list — sink stays clean."""
        srv = _SlowServer()
        sink = _CaptureSink()
        srv.set_progress_sink(sink)

        raw = await srv.handle_request(_request("tool/list", {}))
        body = json.loads(raw)
        assert "result" in body
        assert sink.events == []

    async def test_sink_reset_after_call(self) -> None:
        """After the call returns, the contextvar must not leak the sink."""
        srv = _SlowServer()
        sink = _CaptureSink()
        srv.set_progress_sink(sink)

        await srv.handle_request(_request("tool/call", {"tool_id": "slow.run", "arguments": {}}))
        # The contextvar set inside handle_request was reset on exit.
        assert current_emitter() is None

    async def test_clear_sink(self) -> None:
        srv = _SlowServer()
        sink = _CaptureSink()
        srv.set_progress_sink(sink)
        srv.set_progress_sink(None)

        await srv.handle_request(_request("tool/call", {"tool_id": "slow.run", "arguments": {}}))
        assert sink.events == []


# ---------------------------------------------------------------------------
# ToolProgress validation guard-rails
# ---------------------------------------------------------------------------


class TestToolProgressBounds:
    def test_progress_must_be_in_unit_interval(self) -> None:
        # ToolProgress already declares ge=0.0 / le=1.0; this is a regression
        # canary in case someone widens the range without thinking through
        # the meaning of "fraction complete".
        with pytest.raises(Exception):
            ToolProgress(request_id="r", progress=1.5)
        with pytest.raises(Exception):
            ToolProgress(request_id="r", progress=-0.1)
