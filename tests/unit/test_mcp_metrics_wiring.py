"""The MCP metrics were declared, recorded, tested -- and never wired (FORGE-413).

Prometheus had **zero series** for every `metaforge_mcp_*` metric. Not an
export failure: the sidecar's `build_unified_server(...)` call passed 20
collaborators and not `metrics=`, the factory default is None, and every
recorder returns at its `if counter is not None` guard. Six alert rules could
not fire, two of them added the same day by FORGE-411.

`tests/unit/test_mcp_telemetry.py` already proves the recorders work when a
collector *is* passed, and it passed throughout. So does FORGE-411's test that
the alert selectors use labels the metrics declare -- the metric is declared
correctly and simply never written. Neither test can see this bug, which is
why the ones here assert the wiring itself.

Third instance of one shape: FORGE-406 (approval gate, four tests, no
production caller), the `bootstrap_tool_registry` signature drift (FORGE-405,
FORGE-298), and this.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

_SIDECAR = Path("metaforge/mcp/__main__.py")


def _build_server_call() -> ast.Call:
    """The sidecar's own `build_unified_server(...)` call, from source.

    Read statically rather than by running `_bootstrap`, which opens Neo4j,
    Postgres and aiohttp sessions. The thing that broke is which keywords
    appear at this call site, and that is exactly what this reads.
    """
    tree = ast.parse(_SIDECAR.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_unified_server"
    ]
    assert len(calls) == 1, f"expected one build_unified_server call, found {len(calls)}"
    return calls[0]


class TestTheSidecarWiresWhatItMustWire:
    def test_it_passes_metrics(self) -> None:
        """The bug, as one assertion."""
        keywords = {kw.arg for kw in _build_server_call().keywords}
        assert "metrics" in keywords, (
            "the sidecar builds the MCP server without a metrics collector, so "
            "every metaforge_mcp_* metric records nothing and six alert rules "
            "cannot fire"
        )

    def test_it_passes_the_approval_gate(self) -> None:
        """FORGE-406, pinned here because it is the same failure: a control
        that existed, was tested, and had no production caller. Guarding one
        and not the other would be missing the point."""
        keywords = {kw.arg for kw in _build_server_call().keywords}
        assert "approval_gate" in keywords

    def test_every_metric_recorder_the_server_calls_exists_on_the_collector(self) -> None:
        """A wired collector is no use if the server calls a method it lacks.
        Telemetry is best-effort and swallows exceptions, so a rename here
        would silently stop recording rather than fail."""
        from observability.metrics import MetricsCollector

        for name in (
            "record_mcp_tool_call",
            "record_mcp_error",
            "record_mcp_adapter_probe",
            "record_mcp_code_version",
        ):
            assert callable(getattr(MetricsCollector, name, None)), name


class TestNotNoneIsNotTheRightQuestion:
    def test_a_noop_collector_reports_that_it_is_not_recording(self) -> None:
        """The deeper trap. A no-op collector is not None and records nothing,
        so a caller checking only for None would report healthy telemetry
        while every sample went nowhere."""
        from observability.metrics import MetricsCollector

        assert MetricsCollector().is_recording is False

    def test_a_collector_with_a_meter_and_instruments_is_recording(self) -> None:
        from observability.metrics import MetricsCollector, MetricsRegistry

        class _Instrument:
            def add(self, *_a: Any, **_k: Any) -> None: ...
            def record(self, *_a: Any, **_k: Any) -> None: ...

        class _Meter:
            def create_counter(self, **_k: Any) -> Any:
                return _Instrument()

            def create_histogram(self, **_k: Any) -> Any:
                return _Instrument()

            def create_up_down_counter(self, **_k: Any) -> Any:
                return _Instrument()

        collector = MetricsCollector(meter=_Meter())
        assert collector.is_recording is False  # no instruments created yet
        collector.create_instruments(MetricsRegistry.mcp_metrics())
        assert collector.is_recording is True

    def test_collector_for_returns_a_working_noop_when_no_sdk_is_configured(self) -> None:
        """It must not raise and must not pretend. Recording through it is a
        no-op that cannot fail a tool call."""
        from observability.metrics import collector_for

        collector = collector_for("test-component")
        assert collector.is_recording is False
        collector.record_mcp_code_version("current")  # must not raise


@pytest.mark.asyncio
class TestHealthSaysWhetherAnythingIsRecorded:
    async def _telemetry(self, **kwargs: Any) -> dict[str, Any]:
        from metaforge.mcp.server import UnifiedMcpServer

        server = UnifiedMcpServer(adapters=[], **kwargs)
        raw = await server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health/check", "params": {}})
        )
        return dict(json.loads(raw)["result"]["telemetry"])

    async def test_an_unwired_server_says_not_configured_and_why(self) -> None:
        """Finding this took querying Prometheus and getting zero series back,
        which reads like a quiet system. One call should answer it."""
        report = await self._telemetry()
        assert report["metrics"] == "not configured"
        assert "cannot fire" in report["detail"]

    async def test_a_noop_collector_is_distinguished_from_an_unwired_one(self) -> None:
        from observability.metrics import collector_for

        report = await self._telemetry(metrics=collector_for("test"))
        assert report["metrics"] == "no-op"
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in report["detail"]

    async def test_a_recording_collector_says_recording(self) -> None:
        class _Recording:
            is_recording = True

            def record_mcp_adapter_probe(self, *_a: Any, **_k: Any) -> None: ...
            def record_mcp_code_version(self, *_a: Any, **_k: Any) -> None: ...

        assert (await self._telemetry(metrics=_Recording()))["metrics"] == "recording"

    async def test_a_collaborator_without_the_property_is_unknown_not_asserted(self) -> None:
        """Test doubles predate `is_recording`. Reporting them as recording
        would be a guess; as not recording, a false alarm."""

        class _Old:
            def record_mcp_adapter_probe(self, *_a: Any, **_k: Any) -> None: ...
            def record_mcp_code_version(self, *_a: Any, **_k: Any) -> None: ...

        assert (await self._telemetry(metrics=_Old()))["metrics"] == "unknown"


class TestTheGatewayAndSidecarShareOneImplementation:
    def test_the_gateway_delegates_rather_than_keeping_a_copy(self) -> None:
        """The gateway had this inline. Six lines nobody thought to repeat is
        how the sidecar ended up without them, so there is now one function
        to call instead of six lines to remember."""
        source = Path("api_gateway/server.py").read_text()
        assert "collector_for(" in source
        # The old inline body, which must not come back alongside the call.
        assert "collector.create_instruments(MetricsRegistry.all_metrics())" not in source

    def test_every_alerted_mcp_metric_is_one_the_collector_can_record(self) -> None:
        """Closes the loop the original bug opened: an alert on a metric
        nothing records returns zero results with no error, which reads
        exactly like nothing being wrong."""
        import yaml

        from observability.metrics import MetricsRegistry

        rules = yaml.safe_load(Path("observability/alerting/rules.yaml").read_text())
        alerted = {
            definition.name
            for group in rules["groups"]
            for rule in group["rules"]
            for definition in MetricsRegistry.mcp_metrics()
            if definition.name in rule["expr"]
        }
        assert alerted, "no alert rule references an MCP metric"
        declared = {d.name for d in MetricsRegistry.all_metrics()}
        assert alerted <= declared
