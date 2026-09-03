"""Tool-call hardening in the native loop (MET-569).

Covers the three behaviours the loop gained: batched calls execute
concurrently, an identical repeat inside a turn reuses the first result instead
of re-running the tool, and a failure reaches the model as a structured
envelope instead of ``ERROR: <str(exc)>``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from orchestrator.harness.native_tools import run_native_tools
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tools import ToolRegistry
from orchestrator.harness.validation import ToolValidationError
from skill_registry.mcp_bridge import McpToolError

CONFIG = load_provider_config({"roles": {"generator": [{"provider": "openai", "model": "gpt-4o"}]}})


def _runtime(registry: ToolRegistry, **kwargs: Any) -> HarnessRuntime:
    return HarnessRuntime.build(CONFIG, tools=registry, **kwargs)


def _call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments}


def _scripted(batches: list[list[dict[str, Any]]], final: str = "all done"):
    """A model that emits each batch in turn, then a final text answer."""
    state = {"n": 0}
    seen: list[dict[str, Any]] = []

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        seen.append(request)
        index = state["n"]
        state["n"] += 1
        if index < len(batches):
            return {"text": "", "tool_calls": batches[index]}
        return {"text": final}

    return invoke, seen


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_independent_calls_in_one_batch_overlap(self):
        # Three 100ms calls run serially took ~300ms; concurrently they take
        # ~100ms. The assertion is on observed overlap, not wall-clock, so it
        # does not turn into a flaky timing test on a loaded machine.
        active = {"now": 0, "max": 0}

        async def slow(arguments: dict[str, Any]) -> str:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.05)
            active["now"] -= 1
            return "ok"

        registry = ToolRegistry()
        for name in ("read_a", "read_b", "read_c"):
            registry.register_native(
                name, description=name, input_schema={"type": "object"}, handler=slow
            )

        invoke, _ = _scripted(
            [
                [
                    _call("read_a", {}, "1"),
                    _call("read_b", {}, "2"),
                    _call("read_c", {}, "3"),
                ]
            ]
        )
        result = await run_native_tools(_runtime(registry), "read three things", invoke=invoke)

        assert result.status == "completed"
        assert len(result.steps) == 3
        assert active["max"] == 3

    @pytest.mark.asyncio
    async def test_one_failing_call_does_not_cancel_its_siblings(self):
        async def ok(arguments: dict[str, Any]) -> str:
            return "fine"

        async def boom(arguments: dict[str, Any]) -> str:
            raise RuntimeError("adapter down")

        registry = ToolRegistry()
        registry.register_native(
            "good", description="g", input_schema={"type": "object"}, handler=ok
        )
        registry.register_native(
            "bad", description="b", input_schema={"type": "object"}, handler=boom
        )

        invoke, _ = _scripted([[_call("bad", {}, "1"), _call("good", {}, "2")]])
        result = await run_native_tools(_runtime(registry), "do both", invoke=invoke)

        assert [s.error is not None for s in result.steps] == [True, False]
        assert result.steps[1].observation == "fine"

    @pytest.mark.asyncio
    async def test_results_keep_the_models_call_order(self):
        # Providers match tool results to calls by id; a reordered batch would
        # attach each result to the wrong call.
        async def echo(arguments: dict[str, Any]) -> str:
            await asyncio.sleep(0.02 if arguments.get("slow") else 0)
            return str(arguments.get("tag"))

        registry = ToolRegistry()
        registry.register_native(
            "echo", description="e", input_schema={"type": "object"}, handler=echo
        )

        invoke, seen = _scripted(
            [
                [
                    _call("echo", {"tag": "first", "slow": True}, "call-1"),
                    _call("echo", {"tag": "second"}, "call-2"),
                ]
            ]
        )
        await run_native_tools(_runtime(registry), "echo twice", invoke=invoke)

        tool_messages = [m for m in seen[-1]["messages"] if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_messages] == ["call-1", "call-2"]
        assert "first" in tool_messages[0]["content"]
        assert "second" in tool_messages[1]["content"]

    @pytest.mark.asyncio
    async def test_a_batch_with_an_approval_tool_runs_serially(self):
        # Concurrent approval prompts race for one approver, and a queued call
        # can hit its deny-by-default timeout while the person answers the
        # first — so a batch containing one is executed one call at a time.
        active = {"now": 0, "max": 0}

        async def tracked(arguments: dict[str, Any]) -> str:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.02)
            active["now"] -= 1
            return "ok"

        registry = ToolRegistry()
        registry.register_native(
            "read_it", description="r", input_schema={"type": "object"}, handler=tracked
        )
        registry.register_native(
            "write_it",
            description="w",
            input_schema={"type": "object"},
            handler=tracked,
            requires_approval=True,
        )

        runtime = _runtime(registry)

        # Approve immediately so the turn completes without a real wait.
        async def instant(seconds: float) -> None:
            from orchestrator.harness.runs import ApprovalDecision

            for run in runtime.runs.list():
                runtime.runs.submit_approval(run.id, ApprovalDecision.APPROVE)

        runtime.approval_sleep = instant

        invoke, _ = _scripted([[_call("read_it", {}, "1"), _call("write_it", {}, "2")]])
        await run_native_tools(runtime, "read then write", invoke=invoke)

        assert active["max"] == 1


class TestSameTurnDedup:
    @pytest.mark.asyncio
    async def test_an_identical_repeat_reuses_the_first_result(self):
        runs = {"n": 0}

        async def counted(arguments: dict[str, Any]) -> dict[str, Any]:
            runs["n"] += 1
            return {"value": runs["n"]}

        registry = ToolRegistry()
        registry.register_native(
            "lookup", description="l", input_schema={"type": "object"}, handler=counted
        )

        invoke, seen = _scripted(
            [[_call("lookup", {"id": "N1"}, "1")], [_call("lookup", {"id": "N1"}, "2")]]
        )
        result = await run_native_tools(_runtime(registry), "look twice", invoke=invoke)

        assert runs["n"] == 1
        assert len(result.steps) == 2
        tool_messages = [m for m in seen[-1]["messages"] if m.get("role") == "tool"]
        second = json.loads(tool_messages[1]["content"])
        assert second["cached"] is True
        assert second["result"] == {"value": 1}
        # The trace carries the SAME envelope the model saw, so the reuse is
        # visible to the SSE step feed, the session log, and the eval suite's
        # `no_duplicate_tool_executions` check -- not only to the model.
        assert result.steps[1].observation == second
        assert result.steps[0].observation == {"value": 1}

    @pytest.mark.asyncio
    async def test_duplicates_inside_one_batch_execute_once(self):
        runs = {"n": 0}

        async def counted(arguments: dict[str, Any]) -> str:
            runs["n"] += 1
            return "ok"

        registry = ToolRegistry()
        registry.register_native(
            "lookup", description="l", input_schema={"type": "object"}, handler=counted
        )

        invoke, _ = _scripted(
            [[_call("lookup", {"id": "N1"}, "1"), _call("lookup", {"id": "N1"}, "2")]]
        )
        result = await run_native_tools(_runtime(registry), "look twice at once", invoke=invoke)

        assert runs["n"] == 1
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_different_arguments_are_not_deduplicated(self):
        runs = {"n": 0}

        async def counted(arguments: dict[str, Any]) -> str:
            runs["n"] += 1
            return "ok"

        registry = ToolRegistry()
        registry.register_native(
            "lookup", description="l", input_schema={"type": "object"}, handler=counted
        )

        invoke, _ = _scripted(
            [[_call("lookup", {"id": "N1"}, "1"), _call("lookup", {"id": "N2"}, "2")]]
        )
        await run_native_tools(_runtime(registry), "two lookups", invoke=invoke)

        assert runs["n"] == 2

    @pytest.mark.asyncio
    async def test_a_failed_call_is_not_cached(self):
        # Whatever caused the failure (an adapter restarting, a lock clearing)
        # may have changed, so a retry must really retry.
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

        invoke, _ = _scripted([[_call("retryable", {}, "1")], [_call("retryable", {}, "2")]])
        result = await run_native_tools(_runtime(registry), "retry", invoke=invoke)

        assert runs["n"] == 2
        assert result.steps[1].observation == "ok now"


class TestStructuredErrors:
    @pytest.mark.asyncio
    async def test_a_tool_failure_is_json_not_a_flattened_string(self):
        async def boom(arguments: dict[str, Any]) -> str:
            raise RuntimeError("adapter unreachable")

        registry = ToolRegistry()
        registry.register_native(
            "flaky", description="f", input_schema={"type": "object"}, handler=boom
        )

        invoke, seen = _scripted([[_call("flaky", {}, "1")]])
        await run_native_tools(_runtime(registry), "try it", invoke=invoke)

        content = [m for m in seen[-1]["messages"] if m.get("role") == "tool"][0]["content"]
        payload = json.loads(content)
        assert payload["status"] == "error"
        assert payload["error"] == "adapter unreachable"

    @pytest.mark.asyncio
    async def test_an_mcp_envelope_survives_into_the_tool_result(self):
        async def boom(arguments: dict[str, Any]) -> str:
            raise McpToolError(
                "freecad.export_model",
                "no such object",
                payload={"error": "no such object", "code": -32001, "hint": "open a session"},
            )

        registry = ToolRegistry()
        registry.register_native(
            "export", description="e", input_schema={"type": "object"}, handler=boom
        )

        invoke, seen = _scripted([[_call("export", {}, "1")]])
        await run_native_tools(_runtime(registry), "export", invoke=invoke)

        payload = json.loads(
            [m for m in seen[-1]["messages"] if m.get("role") == "tool"][0]["content"]
        )
        assert payload["tool"] == "freecad.export_model"
        assert payload["details"]["code"] == -32001
        assert payload["details"]["hint"] == "open a session"

    @pytest.mark.asyncio
    async def test_bad_arguments_self_correct_without_executing_the_tool(self):
        # The end-to-end shape of MET-569's first item: the model sends a call
        # missing a required field, gets told so, fixes it, and the tool runs
        # exactly once.
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

        invoke, seen = _scripted(
            [
                [_call("create_primitive", {}, "1")],
                [_call("create_primitive", {"session_id": "s1"}, "2")],
            ]
        )
        result = await run_native_tools(_runtime(registry), "make a box", invoke=invoke)

        assert executed == [{"session_id": "s1"}]
        rejected = [m for m in seen[1]["messages"] if m.get("role") == "tool"][0]
        first = json.loads(rejected["content"])
        assert first["error"] == "invalid_arguments"
        assert any("session_id" in e for e in first["validation_errors"])
        assert result.steps[0].error is not None
        assert isinstance(result.steps[1].observation, str)

    def test_validation_errors_render_as_their_own_payload(self):
        from orchestrator.harness.native_tools import _error_content

        payload = json.loads(_error_content(ToolValidationError("t", ["missing 'x'"])))

        assert payload["error"] == "invalid_arguments"
        assert payload["validation_errors"] == ["missing 'x'"]
