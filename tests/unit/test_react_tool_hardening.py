"""The ReAct loop gets MET-569's dedup + structured errors too.

MET-569 implemented both inside ``run_native_tools``, so any provider whose
adapter cannot parse native ``tool_calls`` (Gemini, Bedrock, …) fell back to
ReAct and kept the old behaviour: repeats re-executed the tool, and an MCP
adapter's error envelope was flattened to ``str(exc)``. Losing a correctness
property because of which provider a deployment picked is a bad trade, so both
now live in ``tool_exec`` and both loops call in.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.harness.react import ReActStep, ToolCall, run_react
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tool_exec import (
    TurnToolCache,
    cached_view,
    dedup_key,
    error_content,
)
from orchestrator.harness.tools import ToolRegistry
from orchestrator.harness.validation import ToolValidationError
from skill_registry.mcp_bridge import McpToolError


class ScriptedPolicy:
    """Emits a fixed sequence of tool calls, then a final answer."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], final: str = "done") -> None:
        self._calls = calls
        self._final = final
        self.seen_steps: list[list[ReActStep]] = []

    async def next_action(self, goal: str, steps: list[ReActStep]) -> Any:
        self.seen_steps.append(list(steps))
        idx = len(steps)
        if idx < len(self._calls):
            name, args = self._calls[idx]
            return _Action(thought=f"step {idx}", tool_call=ToolCall(name, args))
        return _Action(thought="wrap up", tool_call=None, final_output=self._final)


class _Action:
    def __init__(self, thought: str, tool_call: Any, final_output: Any = None) -> None:
        self.thought = thought
        self.tool_call = tool_call
        self.final_output = final_output

    @property
    def is_final(self) -> bool:
        return self.tool_call is None


def _runtime(registry: ToolRegistry) -> HarnessRuntime:
    return HarnessRuntime.build(None, tools=registry)


class TestSharedHelpers:
    def test_dedup_key_is_argument_order_independent(self):
        assert dedup_key("t", {"a": 1, "b": 2}) == dedup_key("t", {"b": 2, "a": 1})

    def test_dedup_key_separates_different_arguments(self):
        # The wild case: a retry that ADDS arguments is a different call.
        bare = dedup_key("project.create", {"name": "x"})
        full = dedup_key("project.create", {"name": "x", "description": "", "status": "draft"})
        assert bare != full

    def test_dedup_key_survives_unserialisable_arguments(self):
        assert dedup_key("t", {"obj": object()})

    def test_cached_view_marks_and_wraps(self):
        view = cached_view({"value": 1})
        assert view["cached"] is True
        assert view["result"] == {"value": 1}
        assert "NOT executed again" in view["note"]

    def test_error_content_passes_an_mcp_envelope_through(self):
        payload = json.loads(
            error_content(
                McpToolError(
                    "freecad.export", "no object", payload={"code": -32001, "hint": "open"}
                )
            )
        )
        assert payload["status"] == "error"
        assert payload["tool"] == "freecad.export"
        assert payload["details"]["code"] == -32001

    def test_error_content_uses_the_validation_payload(self):
        payload = json.loads(error_content(ToolValidationError("t", ["missing 'x'"])))
        assert payload["error"] == "invalid_arguments"

    def test_cache_never_holds_failures_for_the_caller(self):
        cache = TurnToolCache()
        assert cache.has(cache.key("t", {})) is False
        cache.put(cache.key("t", {}), "ok")
        assert cache.has(cache.key("t", {})) is True
        assert len(cache) == 1


class TestReActDedup:
    @pytest.mark.asyncio
    async def test_an_identical_repeat_does_not_re_execute_the_tool(self):
        runs = {"n": 0}

        async def counted(arguments: dict[str, Any]) -> dict[str, Any]:
            runs["n"] += 1
            return {"value": runs["n"]}

        registry = ToolRegistry()
        registry.register_native(
            "lookup", description="l", input_schema={"type": "object"}, handler=counted
        )
        policy = ScriptedPolicy([("lookup", {"id": "N1"}), ("lookup", {"id": "N1"})])

        result = await run_react(_runtime(registry), policy, "look twice", max_steps=5)

        assert runs["n"] == 1
        # Two tool steps plus ReAct's own final-answer step.
        tool_steps = [s for s in result.steps if s.tool_call is not None]
        assert len(tool_steps) == 2
        # The reuse is recorded in the trace, not silently swallowed.
        assert tool_steps[1].observation["cached"] is True
        assert tool_steps[1].observation["result"] == {"value": 1}

    @pytest.mark.asyncio
    async def test_different_arguments_still_execute(self):
        runs = {"n": 0}

        async def counted(arguments: dict[str, Any]) -> str:
            runs["n"] += 1
            return "ok"

        registry = ToolRegistry()
        registry.register_native(
            "lookup", description="l", input_schema={"type": "object"}, handler=counted
        )
        policy = ScriptedPolicy([("lookup", {"id": "N1"}), ("lookup", {"id": "N2"})])

        await run_react(_runtime(registry), policy, "two lookups", max_steps=5)

        assert runs["n"] == 2

    @pytest.mark.asyncio
    async def test_a_failed_call_is_not_cached_so_a_retry_retries(self):
        runs = {"n": 0}

        async def flaky(arguments: dict[str, Any]) -> str:
            runs["n"] += 1
            if runs["n"] == 1:
                raise RuntimeError("adapter starting up")
            return "ok now"

        registry = ToolRegistry()
        registry.register_native(
            "retryable", description="r", input_schema={"type": "object"}, handler=flaky
        )
        policy = ScriptedPolicy([("retryable", {}), ("retryable", {})])

        result = await run_react(_runtime(registry), policy, "retry", max_steps=5)

        assert runs["n"] == 2
        assert result.steps[1].observation == "ok now"


class TestReActStructuredErrors:
    @pytest.mark.asyncio
    async def test_a_tool_failure_reaches_the_policy_as_json(self):
        async def boom(arguments: dict[str, Any]) -> str:
            raise McpToolError(
                "freecad.export_model",
                "no such object",
                payload={"code": -32001, "hint": "open a session first"},
            )

        registry = ToolRegistry()
        registry.register_native(
            "export", description="e", input_schema={"type": "object"}, handler=boom
        )
        policy = ScriptedPolicy([("export", {})])

        result = await run_react(_runtime(registry), policy, "export", max_steps=3)

        payload = json.loads(result.steps[0].error)
        assert payload["status"] == "error"
        assert payload["tool"] == "freecad.export_model"
        # The hint is what lets the model adapt instead of blindly retrying.
        assert payload["details"]["hint"] == "open a session first"

    @pytest.mark.asyncio
    async def test_bad_arguments_are_rejected_without_executing_the_tool(self):
        executed: list[dict[str, Any]] = []

        async def handler(arguments: dict[str, Any]) -> str:
            executed.append(arguments)
            return "created"

        registry = ToolRegistry()
        registry.register_native(
            "create_primitive",
            description="c",
            input_schema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
            handler=handler,
        )
        policy = ScriptedPolicy([("create_primitive", {})])

        result = await run_react(_runtime(registry), policy, "make a box", max_steps=3)

        assert executed == []
        payload = json.loads(result.steps[0].error)
        assert payload["error"] == "invalid_arguments"
        assert "NOT executed" in payload["hint"]
