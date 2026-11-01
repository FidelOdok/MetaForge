"""A guardrail refusal inside a design-flow phase is an observation (FORGE-492).

Shelf run ``run_ea3a0b55d0a74ce1`` ended at its first FreeCAD call: the
sidecar refused ``freecad.open_session`` for the service caller, the refusal
surfaced as ``McpToolError``, and the phase and run failed. The model never got
to choose another tool.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.service_auth import ServiceScopeError, is_service_refusal
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from orchestrator.harness.native_tools import run_native_tools
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tool_exec import note_service_refusal
from orchestrator.harness.tools import ToolRegistry
from skill_registry.mcp_bridge import McpToolError

CONFIG = load_provider_config({"roles": {"generator": [{"provider": "openai", "model": "gpt-4o"}]}})
REFUSAL = str(ServiceScopeError("freecad.execute_code", "may overwrite or remove data"))


class _Metrics:
    def __init__(self) -> None:
        self.refusals: list[tuple[str, str]] = []

    def record_design_flow_tool_refusal(self, tool_name: str, source: str) -> None:
        self.refusals.append((tool_name, source))

    def record_harness_tool_call(self, *args: Any) -> None:
        pass


class TestRecognition:
    def test_the_server_error_and_the_bridge_wrapped_error_both_match(self) -> None:
        assert is_service_refusal(ServiceScopeError("t", "r"))
        assert is_service_refusal(McpToolError("freecad.open_session", REFUSAL))

    def test_an_ordinary_failure_does_not_match(self) -> None:
        assert not is_service_refusal(McpToolError("freecad.measure", "no such object"))
        assert not is_service_refusal(RuntimeError("adapter down"))


class TestModelPath:
    @pytest.mark.asyncio
    async def test_a_refusal_reaches_the_model_and_the_loop_continues(self) -> None:
        async def refused(arguments: dict[str, Any]) -> str:
            raise McpToolError("freecad.execute_code", REFUSAL)

        async def fine(arguments: dict[str, Any]) -> str:
            return "ok"

        registry = ToolRegistry()
        registry.register_native(
            "code", description="c", input_schema={"type": "object"}, handler=refused
        )
        registry.register_native(
            "box", description="b", input_schema={"type": "object"}, handler=fine
        )
        metrics = _Metrics()
        runtime = HarnessRuntime.build(CONFIG, tools=registry, metrics=metrics)

        batches = [
            [{"id": "1", "name": "code", "arguments": {}}],
            [{"id": "2", "name": "box", "arguments": {}}],
        ]
        state = {"n": 0}
        seen: list[dict[str, Any]] = []

        async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
            seen.append(request)
            i = state["n"]
            state["n"] += 1
            return {"text": "", "tool_calls": batches[i]} if i < len(batches) else {"text": "done"}

        result = await run_native_tools(runtime, "build it", invoke=invoke)

        assert result.status == "completed"
        tool_msgs = [m for m in seen[1]["messages"] if m.get("role") == "tool"]
        payload = json.loads(tool_msgs[0]["content"])
        assert payload["status"] == "error"
        assert payload["refused"] is True
        assert "refused for the design-flow service caller" in payload["error"]
        assert "Choose a different tool" in payload["hint"]
        assert metrics.refusals == [("code", "model")]

    def test_noting_an_ordinary_failure_counts_nothing(self) -> None:
        metrics = _Metrics()
        assert not note_service_refusal(metrics, "t", RuntimeError("x"))
        assert metrics.refusals == []


_CTX = FlowContext(goal="g", project_id=None, completed=[])


class TestHandlerPath:
    @pytest.mark.asyncio
    async def test_a_refused_scripted_step_falls_back_to_the_model(self) -> None:
        from api_gateway.runs.mech_handlers import HybridBrain

        class Scripted:
            async def run_phase(
                self, *, goal: str, phase: Phase, context: FlowContext
            ) -> PhaseOutcome:
                raise McpToolError("freecad.open_session", REFUSAL)

        class Model:
            goals: list[str] = []

            async def run_phase(
                self, *, goal: str, phase: Phase, context: FlowContext
            ) -> PhaseOutcome:
                self.goals.append(goal)
                return PhaseOutcome(summary="by model", status="completed")

        model = Model()
        brain = HybridBrain(handlers={"design": Scripted()}, fallback=model)
        out = await brain.run_phase(
            goal="a bracket", phase=Phase(id="design", title="D", objective="o"), context=_CTX
        )

        assert out.summary == "by model"
        assert "a bracket" in model.goals[0]
        assert "was refused" in model.goals[0]
        assert "refused for the design-flow service caller" in model.goals[0]

    @pytest.mark.asyncio
    async def test_any_other_tool_error_still_fails_the_phase(self) -> None:
        from api_gateway.runs.mech_handlers import HybridBrain

        class Scripted:
            async def run_phase(
                self, *, goal: str, phase: Phase, context: FlowContext
            ) -> PhaseOutcome:
                raise McpToolError("freecad.measure", "no such object")

        class Model:
            async def run_phase(
                self, *, goal: str, phase: Phase, context: FlowContext
            ) -> PhaseOutcome:
                raise AssertionError("must not fall back on a real failure")

        brain = HybridBrain(handlers={"design": Scripted()}, fallback=Model())
        with pytest.raises(McpToolError):
            await brain.run_phase(
                goal="g", phase=Phase(id="design", title="D", objective="o"), context=_CTX
            )
