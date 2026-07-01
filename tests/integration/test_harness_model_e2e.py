"""End-to-end harness integration (MET-548): model → ReAct → gated tools →
run lifecycle → ledger, plus provider failover. Network-free (scripted invoke).

This exercises every layer the MET-547 harness + MET-548 integration built,
wired together the way the chat backend / CLI drive it.
"""

from __future__ import annotations

import pytest

from orchestrator.harness import NativeToolDef, build_agent_runtime
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.policy import ModelPolicy
from orchestrator.harness.providers import ProviderError, ProviderSpec, load_provider_config
from orchestrator.harness.react import run_react
from orchestrator.harness.runs import RunStatus

ONE_PROVIDER = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)


def _scripted_invoke(*replies: str):
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        i = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return {"text": replies[i], "model": spec.model}

    return invoke


async def _twin_search(args: dict[str, object]) -> dict[str, object]:
    return {"hits": ["M3 bolt", "M4 bolt"]}


async def _apply_change(args: dict[str, object]) -> dict[str, object]:
    return {"applied": args.get("param")}


@pytest.mark.asyncio
async def test_model_drives_gated_tools_with_run_and_ledger() -> None:
    """Happy path: model searches, applies a gated change (approved), finalizes;
    the run completes and the ledger persists + full-text-searches its events."""
    ctx = build_agent_runtime(
        ONE_PROVIDER,
        native_tools=[
            NativeToolDef("twin_search", "search the twin", {}, _twin_search),
            NativeToolDef("apply_change", "mutate", {}, _apply_change, required_gates=("apply",)),
        ],
        gate_check=lambda g: g == "apply",  # approved
    )
    rt = ctx.runtime
    rt.runs.create({"goal": "pick a bolt and apply"}, run_id="r1")
    rt.runs.start("r1")

    policy = ModelPolicy(
        rt,
        invoke=_scripted_invoke(
            '{"thought": "look up bolts", "tool": "twin_search", "arguments": {"q": "bolt"}}',
            '{"thought": "apply it", "tool": "apply_change", "arguments": {"param": "M3"}}',
            '{"thought": "done", "final": "applied M3 bolt"}',
        ),
    )
    result = await run_react(rt, policy, "pick a bolt and apply", max_steps=6)

    # ReAct threaded model → tools correctly.
    assert result.status == "completed"
    assert result.output == "applied M3 bolt"
    assert result.steps[0].observation == {"hits": ["M3 bolt", "M4 bolt"]}
    assert result.steps[1].observation == {"applied": "M3"}  # gate allowed the mutation

    # Run lifecycle + ledger persistence.
    rt.runs.complete("r1", result={"answer": result.output})
    ledger = SqliteRunLedger(":memory:")
    ledger.record_run(rt.runs.get("r1"))
    for step in result.steps:
        if step.tool_call is not None:
            ledger.record_event("r1", "action", f"{step.tool_call.name} -> {step.observation}")

    assert rt.runs.get("r1").status is RunStatus.COMPLETED
    assert ledger.get_run("r1")["status"] == "completed"
    assert [e.run_id for e in ledger.search("apply_change")] == ["r1"]


@pytest.mark.asyncio
async def test_gate_denied_is_surfaced_not_fatal() -> None:
    """When the gate denies the mutation, the tool is blocked, the error is fed
    back to the model, and the model can still finalize gracefully."""
    ctx = build_agent_runtime(
        ONE_PROVIDER,
        native_tools=[
            NativeToolDef("apply_change", "mutate", {}, _apply_change, required_gates=("apply",)),
        ],
        gate_check=lambda g: False,  # denied
    )
    policy = ModelPolicy(
        ctx.runtime,
        invoke=_scripted_invoke(
            '{"tool": "apply_change", "arguments": {"param": "M3"}}',
            '{"final": "could not apply — approval required"}',
        ),
    )
    result = await run_react(ctx.runtime, policy, "apply a change", max_steps=4)
    assert result.status == "completed"
    assert "approval required" in result.output
    assert result.steps[0].error is not None
    assert "gate 'apply'" in result.steps[0].error


@pytest.mark.asyncio
async def test_provider_failover_end_to_end() -> None:
    """A 429 from the primary provider fails over to the fallback within a
    single ReAct step, transparently to the policy."""
    cfg = load_provider_config(
        {
            "retry": {"api_max_retries": 0},
            "roles": {
                "generator": [
                    {"provider": "anthropic", "model": "claude-opus-4-8"},
                    {"provider": "openai", "model": "gpt-5"},
                ]
            },
        }
    )
    ctx = build_agent_runtime(cfg)

    async def failover_invoke(spec: ProviderSpec, request: object) -> dict:
        if spec.name == "anthropic":
            raise ProviderError("rate limited", status_code=429)
        return {"text": '{"final": "answered by fallback"}', "model": spec.model}

    policy = ModelPolicy(ctx.runtime, invoke=failover_invoke)
    result = await run_react(ctx.runtime, policy, "hi", max_steps=2)
    assert result.status == "completed"
    assert result.output == "answered by fallback"
