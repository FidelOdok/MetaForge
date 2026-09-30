"""MCP failures have to reach the observability stack (FORGE-379).

The MCP server is where every external harness meets MetaForge, and it had
logs and one span and nothing else: no metrics at all, and no
``record_exception`` anywhere in the module. So "which plugin tool is
failing, for whom" was a question you answered by reading Loki by hand,
and a failed call produced a Tempo span that looked like a request which
happened to return.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer, error_class
from tool_registry.mcp_server.handlers import ToolHandlerError, ToolManifest, ToolNotFoundError
from tool_registry.mcp_server.server import McpToolServer


class _Recorder:
    """Stands in for MetricsCollector."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.errors: list[tuple] = []
        self.probes: list[tuple] = []

    def record_mcp_tool_call(self, tool_id, status, duration, client="unknown") -> None:
        self.calls.append((tool_id, status, duration, client))

    def record_mcp_error(self, tool_id, error_class, client="unknown") -> None:
        self.errors.append((tool_id, error_class, client))

    def record_mcp_adapter_probe(self, adapter_id, reachable) -> None:
        self.probes.append((adapter_id, reachable))


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        for tool_id in ("twin.get_node", "twin.commit_geometry"):
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="twin",
                    name=tool_id,
                    description="stub",
                    capability="test",
                ),
                self._ok,
            )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("boom"):
            raise ValueError("adapter blew up")
        return {"node_id": "n-1"}


def _initialise(server: UnifiedMcpServer, name: str = "claude-code") -> None:
    asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": name, "version": "1"},
                    },
                }
            )
        )
    )


def _call(server: UnifiedMcpServer, tool: str, args: dict[str, Any] | None = None) -> dict:
    raw = asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "2",
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args or {}},
                }
            )
        )
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# The error taxonomy
# ---------------------------------------------------------------------------


def test_known_failures_are_classified() -> None:
    assert error_class(ToolNotFoundError("twin.nope")) == "tool_not_found"
    assert error_class(ToolHandlerError("twin.x", "boom", 1.0)) == "tool_execution_error"


def test_an_unclassified_failure_stands_out() -> None:
    """Folding it into tool_execution_error would hide a new failure mode
    inside a bucket someone is already ignoring."""
    assert error_class(RuntimeError("something new")) == "unexpected"


def test_a_subclass_is_still_classified() -> None:
    class Weird(ToolNotFoundError):
        pass

    assert error_class(Weird("twin.nope")) == "tool_not_found"


def test_the_taxonomy_is_not_a_second_list() -> None:
    """It matches on the exception types the server already raises. A
    parallel list is one that can disagree with the errors clients
    actually receive."""
    from metaforge.mcp.server import _ERROR_CLASSES

    names = {n for n, _ in _ERROR_CLASSES}
    for exc in ("ToolNotFoundError", "ToolHandlerError", "ApprovalRejectedError"):
        assert exc in names


# ---------------------------------------------------------------------------
# Calls and failures reach the collector
# ---------------------------------------------------------------------------


def test_a_successful_call_is_counted_with_its_client() -> None:
    from metaforge.mcp.capture import SessionCapture

    class _Store:
        async def create_session(self, **kw: Any) -> Any:
            return type("S", (), {"id": "s1"})()

        async def append_event(self, *a: Any, **k: Any) -> tuple[str, int]:
            return ("e", 1)

    recorder = _Recorder()
    server = UnifiedMcpServer(
        [_Adapter()], metrics=recorder, session_capture=SessionCapture(_Store())
    )
    _initialise(server, name="codex")
    _call(server, "twin.get_node")
    assert recorder.calls and recorder.calls[0][0] == "twin.get_node"
    assert recorder.calls[0][1] == "ok"
    assert recorder.calls[0][3] == "codex"


def test_a_failed_call_is_counted_and_classified() -> None:
    from metaforge.mcp.capture import SessionCapture

    class _Store:
        async def create_session(self, **kw: Any) -> Any:
            return type("S", (), {"id": "s1"})()

        async def append_event(self, *a: Any, **k: Any) -> tuple[str, int]:
            return ("e", 1)

    recorder = _Recorder()
    server = UnifiedMcpServer(
        [_Adapter()], metrics=recorder, session_capture=SessionCapture(_Store())
    )
    _initialise(server)
    _call(server, "twin.get_node", {"boom": True})
    assert recorder.calls[0][1] == "error"
    assert recorder.errors[0][1] == "tool_execution_error"


def test_an_unlabelled_client_is_unknown_not_missing() -> None:
    """A metric attribute that is sometimes absent makes a PromQL `sum by`
    silently split one series into two."""
    from metaforge.mcp.capture import SessionCapture

    class _Store:
        async def create_session(self, **kw: Any) -> Any:
            return type("S", (), {"id": "s1"})()

        async def append_event(self, *a: Any, **k: Any) -> tuple[str, int]:
            return ("e", 1)

    recorder = _Recorder()
    server = UnifiedMcpServer(
        [_Adapter()], metrics=recorder, session_capture=SessionCapture(_Store())
    )
    _call(server, "twin.get_node")  # no initialize
    assert recorder.calls[0][3] == "unknown"


def test_metrics_never_fail_a_tool_call() -> None:
    """The same contract session capture keeps: telemetry that can break a
    call is worse than no telemetry."""
    from metaforge.mcp.capture import SessionCapture

    class _Store:
        async def create_session(self, **kw: Any) -> Any:
            return type("S", (), {"id": "s1"})()

        async def append_event(self, *a: Any, **k: Any) -> tuple[str, int]:
            return ("e", 1)

    class _Exploding(_Recorder):
        def record_mcp_tool_call(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("collector down")

    server = UnifiedMcpServer(
        [_Adapter()], metrics=_Exploding(), session_capture=SessionCapture(_Store())
    )
    assert "result" in _call(server, "twin.get_node")


def test_no_collector_is_fine() -> None:
    server = UnifiedMcpServer([_Adapter()])
    assert "result" in _call(server, "twin.get_node")


# ---------------------------------------------------------------------------
# Health probes become a time series
# ---------------------------------------------------------------------------


def test_each_probe_is_recorded() -> None:
    """FORGE-332 made the probe real; this is what lets an alert fire on it
    rather than waiting for someone to run the doctor."""
    recorder = _Recorder()
    server = UnifiedMcpServer([_Adapter()], metrics=recorder)
    asyncio.run(server._health_check())
    assert recorder.probes == [("twin", True)]


def test_a_down_adapter_is_recorded_as_unreachable() -> None:
    class _Down(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="calculix", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="calculix.run_fea",
                    adapter_id="calculix",
                    name="calculix.run_fea",
                    description="stub",
                    capability="test",
                ),
                self._ok,
            )

        async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def handle_request(self, raw: str) -> str:
            raise RuntimeError("container is down (-32001)")

    recorder = _Recorder()
    server = UnifiedMcpServer([_Adapter(), _Down()], metrics=recorder)
    asyncio.run(server._health_check())
    assert ("calculix", False) in recorder.probes


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


def test_failures_are_recorded_on_the_span() -> None:
    """There were no record_exception calls anywhere in this module, which
    is the difference between a Tempo span that shows a failure and one
    that shows a request that happened to return."""
    recorded: list[BaseException] = []
    attrs: dict[str, Any] = {}

    class _Span:
        def record_exception(self, exc: BaseException) -> None:
            recorded.append(exc)

        def set_attribute(self, key: str, value: Any) -> None:
            attrs[key] = value

    UnifiedMcpServer._mark_failed(_Span(), ToolNotFoundError("twin.nope"))
    assert recorded and attrs["mcp.error_class"] == "tool_not_found"


def test_a_broken_tracer_does_not_break_the_call() -> None:
    class _Span:
        def record_exception(self, exc: BaseException) -> None:
            raise RuntimeError("tracer down")

        def set_attribute(self, key: str, value: Any) -> None:
            pass

    UnifiedMcpServer._mark_failed(_Span(), ToolNotFoundError("x"))


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("twin.does_not_exist", {}, "tool_not_found"),
        ("twin.get_node", {"boom": True}, "tool_execution_error"),
    ],
)
def test_the_error_paths_actually_mark_the_span(
    monkeypatch: pytest.MonkeyPatch, tool: str, args: dict[str, Any], expected: str
) -> None:
    """Exercising the helper in isolation is not enough -- the first version
    of this test passed with every call site deleted. What matters is that
    the paths a client actually hits reach it."""
    marked: list[str] = []

    def _spy(span: Any, exc: BaseException) -> None:
        marked.append(error_class(exc))

    monkeypatch.setattr(UnifiedMcpServer, "_mark_failed", staticmethod(_spy))
    server = UnifiedMcpServer([_Adapter()])
    assert "error" in _call(server, tool, args)
    assert marked == [expected]


# ---------------------------------------------------------------------------
# The metrics are declared and alerted on
# ---------------------------------------------------------------------------


def test_the_metrics_are_in_the_registry() -> None:
    from observability.metrics import MetricsRegistry

    names = {m.name for m in MetricsRegistry.all_metrics()}
    for metric in MetricsRegistry.mcp_metrics():
        assert metric.name in names, f"{metric.name} is defined but not in all_metrics()"


def test_the_probe_metric_is_a_counter_not_a_gauge() -> None:
    """`type="gauge"` in this registry creates an OTel UpDownCounter, whose
    add() is a delta. Writing 1 then 0 to it would climb and never come
    back down, so a "reachable" gauge would read healthy forever."""
    from observability.metrics import MetricsRegistry

    assert MetricsRegistry.MCP_ADAPTER_PROBE_TOTAL.type == "counter"


def test_every_new_metric_has_an_alert_rule() -> None:
    """CLAUDE.md asks for one, and a metric nobody alerts on is a metric
    nobody reads."""
    from pathlib import Path

    import yaml

    rules = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2] / "observability" / "alerting" / "rules.yaml"
        ).read_text()
    )
    expressions = " ".join(
        r["expr"] for g in rules["groups"] for r in g["rules"] if g["name"] == "metaforge_mcp"
    )
    assert "metaforge_mcp_adapter_probe_total" in expressions
    assert "metaforge_mcp_error_total" in expressions
    assert "metaforge_mcp_tool_call_total" in expressions
