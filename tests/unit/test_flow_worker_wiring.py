"""The design-flow worker gets tools, a project scope and a visible model failure (FORGE-475).

Live on fidel-dev the worker built every phase with ``mcp_tools=0`` (its
process never wires the gateway's MCP bridge) and against a provider it had
no key for, so phases ran toolless and then failed without saying why. These
pin the three fixes in ``api_gateway/runs/flow_worker.py``:

* the sidecar bridge is connected once and installed where
  ``build_phase_brain`` reads it, and the harness then registers its tools;
* a phase's MCP calls carry the run's project;
* a phase that cannot reach any model fails the activity with that reason,
  non-retryable when no retry could help.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from structlog.testing import capture_logs
from temporalio.exceptions import ApplicationError

from api_gateway.chat import routes as chat_routes
from api_gateway.runs import flow_worker
from mcp_core.context import current_context
from orchestrator.design_flow.executor import PhaseOutcome
from orchestrator.design_flow.frozen import FrozenPhase
from orchestrator.design_flow.temporal_flow import PhaseRequest
from orchestrator.harness.providers.pipeline import (
    AllProvidersFailedError,
    ProviderError,
    ProviderSpec,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge

PROJECT = "3f2c5a64-1e9b-4c55-9f0e-0d8a6c1b7e21"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    # No real ~/.metaforge selection leaks into provider resolution.
    monkeypatch.setenv("HOME", str(tmp_path))
    previous = chat_routes.get_mcp_bridge()
    flow_worker._reset_mcp_bridge()
    yield
    flow_worker._reset_mcp_bridge()
    chat_routes.init_mcp_bridge(previous)


def _sidecar_bridge() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.record_decision", "twin", input_schema={"type": "object"})
    bridge.register_tool("project.get", "project", input_schema={"type": "object"})
    return bridge


def _fake_connect(bridge: Any, calls: list[dict[str, Any]]) -> Any:
    async def connect(url: str, **kwargs: Any) -> Any:
        calls.append({"url": url, **kwargs})
        return bridge

    return connect


def _request(project_id: str | None = PROJECT) -> PhaseRequest:
    phase = FrozenPhase(
        id="design",
        title="Design",
        objective="Design the part",
        disciplines=["mechanical"],
    )
    return PhaseRequest(
        run_id="run-123",
        goal="a bracket",
        phase=phase,
        project_id=project_id,
        session_id=None,
        flow_id="custom",
    )


class TestServerUrl:
    def test_defaults_to_the_compose_sidecar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("METAFORGE_MCP_URL", raising=False)
        assert flow_worker.mcp_server_url() == "http://mcp-http:8765"

    @pytest.mark.parametrize(
        "raw", ["http://side:9000/mcp", "http://side:9000/mcp/", "http://side:9000"]
    )
    def test_never_doubles_the_mcp_path(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        # HttpTransport appends /mcp itself.
        monkeypatch.setenv("METAFORGE_MCP_URL", raw)
        assert flow_worker.mcp_server_url() == "http://side:9000"


class TestEnsureBridge:
    async def test_installs_the_sidecar_bridge_where_the_phase_brain_reads_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bridge = _sidecar_bridge()
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "skill_registry.bridge_factory.connect_http_bridge", _fake_connect(bridge, calls)
        )
        monkeypatch.setenv("METAFORGE_MCP_CLIENT_KEY", "k")

        assert await flow_worker.ensure_mcp_bridge() is bridge
        assert await flow_worker.ensure_mcp_bridge() is bridge
        assert chat_routes.get_mcp_bridge() is bridge
        # Connected once, required (no silent in-memory fallback), keyed.
        assert len(calls) == 1
        assert calls[0]["require"] is True
        assert calls[0]["api_key"] == "k"

    async def test_an_unreachable_sidecar_fails_the_phase_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refuse(url: str, **kwargs: Any) -> Any:
            raise RuntimeError("connection refused")

        monkeypatch.setattr("skill_registry.bridge_factory.connect_http_bridge", refuse)
        with pytest.raises(ApplicationError) as err:
            await flow_worker.ensure_mcp_bridge()
        assert "mcp-http:8765" in str(err.value)
        assert "connection refused" in str(err.value)
        # Retryable: the sidecar may simply not be up yet.
        assert err.value.non_retryable is False
        # Not cached, so the next phase tries again.
        assert flow_worker._bridge is None

    async def test_a_sidecar_with_no_tools_is_not_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "skill_registry.bridge_factory.connect_http_bridge",
            _fake_connect(InMemoryMcpBridge(), []),
        )
        with pytest.raises(ApplicationError, match="lists no tools"):
            await flow_worker.ensure_mcp_bridge()

    async def test_the_phase_brain_registers_the_sidecar_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The live symptom was ``agent_runtime_built mcp_tools=0``."""
        from api_gateway.chat.harness_backend import _build_context
        from api_gateway.runs.routes import build_phase_brain
        from orchestrator.harness.providers import CredentialStore

        bridge = _sidecar_bridge()
        monkeypatch.setattr(
            "skill_registry.bridge_factory.connect_http_bridge", _fake_connect(bridge, [])
        )
        await flow_worker.ensure_mcp_bridge()

        brain = await build_phase_brain("run-123", "custom")
        react = brain._fallback  # the brain an unhandled phase runs on
        assert react._bridge is bridge

        with capture_logs() as logs:
            await _build_context("flow:run-123:design", CredentialStore(), react._bridge)
        built = [e for e in logs if e["event"] == "agent_runtime_built"]
        assert built and built[-1]["mcp_tools"] > 0


class _ScopeRecordingBrain:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.seen: Any = None

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> PhaseOutcome:
        self.seen = current_context()
        if self.error is not None:
            raise self.error
        return PhaseOutcome(summary="done", artifacts=[], status="completed")


def _patch_brain(monkeypatch: pytest.MonkeyPatch, brain: _ScopeRecordingBrain) -> None:
    async def build(run_id: str, flow_id: str | None = None) -> Any:
        return brain

    async def ready() -> Any:
        return None

    monkeypatch.setattr("api_gateway.runs.routes.build_phase_brain", build)
    monkeypatch.setattr(flow_worker, "ensure_mcp_bridge", ready)


class TestRunPhase:
    async def test_tool_calls_are_scoped_to_the_runs_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        brain = _ScopeRecordingBrain()
        _patch_brain(monkeypatch, brain)

        result = await flow_worker._run_phase(_request())

        assert result.status == "completed"
        assert brain.seen.project_id == uuid.UUID(PROJECT)
        assert brain.seen.actor_id == "agent:design-flow"
        # One stable session per run, so capture groups its phases together.
        again = _ScopeRecordingBrain()
        _patch_brain(monkeypatch, again)
        await flow_worker._run_phase(_request())
        assert again.seen.session_id == brain.seen.session_id
        # And the scope does not outlive the phase.
        assert current_context().project_id is None

    async def test_a_missing_key_fails_the_run_once_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = ProviderSpec(name="anthropic", model="claude-opus-4-8")
        missing = ProviderError("missing API key in env 'METAFORGE_LLM_API_KEY'")
        _patch_brain(
            monkeypatch,
            _ScopeRecordingBrain(AllProvidersFailedError("generator", [(spec, missing)])),
        )

        with pytest.raises(ApplicationError) as err:
            await flow_worker._run_phase(_request())

        assert "missing API key in env 'METAFORGE_LLM_API_KEY'" in str(err.value)
        assert err.value.type == "ProviderUnavailable"
        # Retrying cannot conjure a key: fail once instead of three times.
        assert err.value.non_retryable is True

    async def test_a_transient_provider_failure_stays_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = ProviderSpec(name="anthropic", model="claude-opus-4-8")
        busy = ProviderError("rate limited", status_code=429, retryable=True)
        _patch_brain(
            monkeypatch, _ScopeRecordingBrain(AllProvidersFailedError("generator", [(spec, busy)]))
        )

        with pytest.raises(ApplicationError) as err:
            await flow_worker._run_phase(_request())
        assert err.value.non_retryable is False

    async def test_an_unrelated_error_is_not_relabelled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_brain(monkeypatch, _ScopeRecordingBrain(KeyError("boom")))
        with pytest.raises(KeyError):
            await flow_worker._run_phase(_request())
