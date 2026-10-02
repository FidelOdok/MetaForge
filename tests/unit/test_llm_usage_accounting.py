"""Token and cost accounting per LLM call (FORGE-476). Network-free.

Fake SDK clients of each provider family go through the real adapters and the
real pipeline, and the recorded events are asserted for tokens, cached tokens,
cost and attribution.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from observability.metrics import MetricsRegistry
from orchestrator.harness.providers import ProviderSpec
from orchestrator.harness.providers.adapters import (
    anthropic_invoke,
    codex_invoke,
    openai_invoke,
)
from orchestrator.harness.providers.pipeline import ProviderPipeline, RoleModelSlots
from orchestrator.harness.providers.usage import (
    TokenUsage,
    UsageStore,
    configure_usage_store,
    price_cost_usd,
    record_call,
    usage_report,
    usage_scope,
)


@pytest.fixture()
def store() -> Iterator[UsageStore]:
    s = UsageStore(":memory:")
    configure_usage_store(s)
    yield s
    configure_usage_store(None)


class _Metrics:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.exceeded: list[str] = []

    def record_llm_usage(self, **kw: Any) -> None:
        self.calls.append(kw)

    def record_llm_run_spend_exceeded(self, role: str) -> None:
        self.exceeded.append(role)


def _anthropic_client() -> Any:
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=400,
        cache_creation_input_tokens=200,
    )
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi")],
        model="claude-sonnet-5",
        stop_reason="end_turn",
        usage=usage,
    )

    async def create(**_: Any) -> Any:
        return resp

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _openai_client() -> Any:
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=40,
        prompt_tokens_details=SimpleNamespace(cached_tokens=600),
    )
    msg = SimpleNamespace(content="hello", tool_calls=None)
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        model="gpt-5.5",
        usage=usage,
    )

    async def create(**_: Any) -> Any:
        return resp

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _codex_client() -> Any:
    usage = SimpleNamespace(
        input_tokens=300,
        output_tokens=30,
        input_tokens_details=SimpleNamespace(cached_tokens=100),
    )

    class _Stream:
        def __aiter__(self) -> AsyncIterator[Any]:
            async def gen() -> AsyncIterator[Any]:
                yield SimpleNamespace(type="response.output_text.delta", delta="ok")
                yield SimpleNamespace(
                    type="response.completed", response=SimpleNamespace(usage=usage)
                )

            return gen()

    async def create(**_: Any) -> Any:
        return _Stream()

    return SimpleNamespace(responses=SimpleNamespace(create=create))


REQ = {"messages": [{"role": "user", "content": "x"}]}


def test_token_usage_normalises_families() -> None:
    anth = TokenUsage.from_provider(
        {"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 400}
    )
    assert anth is not None and anth.prompt == 500 and anth.cached_input == 400
    oai = TokenUsage.from_provider(
        {
            "input_tokens": 1000,
            "output_tokens": 5,
            "cached_input_tokens": 600,
            "input_includes_cached": True,
        }
    )
    assert oai is not None and oai.prompt == 1000 and oai.cached_input == 600
    assert TokenUsage.from_provider(None) is None


def test_unknown_model_cost_is_none_never_zero() -> None:
    tokens = TokenUsage(prompt=10, completion=10)
    assert price_cost_usd("anthropic", "no-such-model", tokens) is None
    assert price_cost_usd("anthropic", "claude-sonnet-5", None) is None
    assert price_cost_usd("anthropic", "claude-sonnet-5", tokens) is not None


async def test_anthropic_tokens_cached_cost_and_attribution(store: UsageStore) -> None:
    spec = ProviderSpec(name="anthropic", model="claude-sonnet-5")
    pipe = ProviderPipeline(RoleModelSlots({"generator": [spec]}))
    metrics = _Metrics()
    pipe._metrics = metrics  # type: ignore[assignment]

    async def invoke(s: ProviderSpec, r: Any) -> Any:
        return await anthropic_invoke(s, r, client=_anthropic_client())

    with usage_scope(run_id="run-1", phase="design", role="phase_brain", caller="cli"):
        await pipe.complete("generator", REQ, invoke)

    totals = store.run_totals("run-1")
    assert totals is not None
    assert totals["prompt_tokens"] == 700  # 100 + 400 read + 200 written
    assert totals["completion_tokens"] == 50
    assert totals["cached_input_tokens"] == 400
    # 100 fresh*3 + 400 cached*0.3 + 200 written*3.75 + 50 out*15, per 1M
    assert totals["cost_usd"] == pytest.approx((300 + 120 + 750 + 750) / 1_000_000, rel=1e-6)
    assert totals["by_phase"]["design"]["calls"] == 1
    assert totals["by_role"]["phase_brain"]["prompt_tokens"] == 700
    assert metrics.calls[0]["role"] == "phase_brain"
    assert metrics.calls[0]["cached_input"] == 400


async def test_openai_cached_tokens_not_double_counted(store: UsageStore) -> None:
    spec = ProviderSpec(name="openai", model="gpt-5.5")
    pipe = ProviderPipeline(RoleModelSlots({"generator": [spec]}))

    async def invoke(s: ProviderSpec, r: Any) -> Any:
        return await openai_invoke(s, r, client=_openai_client())

    with usage_scope(run_id="run-2", phase="requirements", role="flow_generator"):
        await pipe.complete("generator", REQ, invoke)

    totals = store.run_totals("run-2")
    assert totals is not None
    assert totals["prompt_tokens"] == 1000
    assert totals["cached_input_tokens"] == 600
    # 400 fresh*5 + 600 cached*0.5 + 40 out*15, per 1M
    assert totals["cost_usd"] == pytest.approx((2000 + 300 + 600) / 1_000_000, rel=1e-6)
    assert "flow_generator" in totals["by_role"]


async def test_codex_usage_from_completed_event(store: UsageStore) -> None:
    spec = ProviderSpec(name="openai-codex", model="gpt-5.5")
    pipe = ProviderPipeline(RoleModelSlots({"generator": [spec]}))

    async def invoke(s: ProviderSpec, r: Any) -> Any:
        return await codex_invoke(s, r, client=_codex_client())

    with usage_scope(run_id="run-3", role="chat"):
        await pipe.complete("generator", REQ, invoke)

    totals = store.run_totals("run-3")
    assert totals is not None
    assert (totals["prompt_tokens"], totals["completion_tokens"]) == (300, 30)
    assert totals["cached_input_tokens"] == 100


async def test_unreported_usage_is_counted_not_zeroed(store: UsageStore) -> None:
    spec = ProviderSpec(name="gemini", model="whatever")
    pipe = ProviderPipeline(RoleModelSlots({"generator": [spec]}))

    async def invoke(s: ProviderSpec, r: Any) -> Any:
        return {"text": "no usage here"}

    with usage_scope(run_id="run-4", role="gate_check"):
        await pipe.complete("generator", REQ, invoke)

    totals = store.run_totals("run-4")
    assert totals is not None
    assert totals["calls"] == 1
    assert totals["calls_without_usage"] == 1
    assert totals["calls_unpriced"] == 1


async def test_event_stream_records_final_response_usage(store: UsageStore) -> None:
    spec = ProviderSpec(name="anthropic", model="claude-sonnet-5")
    pipe = ProviderPipeline(RoleModelSlots({"generator": [spec]}))

    async def events(s: ProviderSpec, r: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "text_delta", "text": "a"}
        yield {
            "type": "response",
            "result": {"text": "a", "usage": {"input_tokens": 7, "output_tokens": 3}},
        }

    with usage_scope(run_id="run-5", role="chat"):
        seen = [e async for e in pipe.stream_events_complete("generator", REQ, events)]

    assert [e["type"] for e in seen] == ["text_delta", "response"]
    totals = store.run_totals("run-5")
    assert totals is not None and totals["calls"] == 1
    assert (totals["prompt_tokens"], totals["completion_tokens"]) == (7, 3)


def test_scope_inherits_and_default_role_does_not_override() -> None:
    from orchestrator.harness.providers.usage import current_usage_context

    with usage_scope(run_id="r", phase="p", role="phase_brain"):
        with usage_scope(default_role="chat"):
            ctx = current_usage_context()
            assert (ctx.run_id, ctx.phase, ctx.role) == ("r", "p", "phase_brain")
    with usage_scope(default_role="chat"):
        assert current_usage_context().role == "chat"


def test_runaway_spend_fires_once_per_run(
    store: UsageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_RUN_SPEND_ALERT_USD", "0.00001")
    metrics = _Metrics()
    usage = {"input_tokens": 1000, "output_tokens": 1000}
    with usage_scope(run_id="big", role="phase_brain"):
        for _ in range(3):
            record_call(
                provider="anthropic",
                model="claude-sonnet-5",
                pipeline_role="generator",
                usage=usage,
                metrics=metrics,  # type: ignore[arg-type]
            )
    assert metrics.exceeded == ["phase_brain"]


def test_summary_window_and_report(store: UsageStore) -> None:
    with usage_scope(run_id="a", role="chat"):
        record_call(
            provider="openai",
            model="gpt-5.5",
            pipeline_role="generator",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
    summary = store.summary_since(86400)
    assert summary["calls"] == 1 and summary["window_seconds"] == 86400
    assert store.summary_since(1, now=10**12)["calls"] == 0
    assert usage_report()["available"] is True


def test_price_table_is_a_data_file_and_metrics_registered() -> None:
    from orchestrator.harness.providers import usage

    assert usage._prices_path().name == "model_prices.json"
    assert json.loads(usage._prices_path().read_text())["prices"]
    names = {m.name for m in MetricsRegistry.harness_metrics()}
    assert {
        "metaforge_llm_tokens_total",
        "metaforge_llm_cost_usd_total",
        "metaforge_llm_run_spend_exceeded_total",
    } <= names
    tokens_def = MetricsRegistry.LLM_TOKENS_TOTAL
    assert tokens_def.labels == ["role", "provider", "model", "kind"]


# ── run status exposes totals per phase (FORGE-476 acceptance) ────────────


def test_run_status_and_flow_state_show_totals_per_phase(store: UsageStore) -> None:
    from fastapi.testclient import TestClient

    from api_gateway.server import create_app

    client = TestClient(create_app())
    created = client.post(
        "/v1/runs",
        json={
            "request": {"kind": "design_flow", "flow": "mech_v1", "goal": "g"},
            "start": False,
        },
    )
    run_id = created.json()["id"]
    assert client.get(f"/v1/runs/{run_id}").json()["usage"] is None

    with usage_scope(run_id=run_id, phase="design", role="phase_brain"):
        record_call(
            provider="anthropic",
            model="claude-sonnet-5",
            pipeline_role="generator",
            usage={"input_tokens": 100, "output_tokens": 20},
        )
    with usage_scope(run_id=run_id, phase="design", role="gate_check"):
        record_call(
            provider="anthropic",
            model="unlisted-model",
            pipeline_role="generator",
            usage={"input_tokens": 10, "output_tokens": 2},
        )

    status = client.get(f"/v1/runs/{run_id}").json()["usage"]
    assert status["prompt_tokens"] == 110
    assert status["by_phase"]["design"]["calls"] == 2
    assert set(status["by_role"]) == {"phase_brain", "gate_check"}
    # One call has no price: the total is a lower bound and says so.
    assert status["calls_unpriced"] == 1

    state = client.get(f"/v1/runs/{run_id}/flow-state").json()
    assert state["usage"]["completion_tokens"] == 22
    by_id = {p["id"]: p for p in state["phases"]}
    assert by_id["design"]["usage"]["prompt_tokens"] == 110

    summary = client.get("/v1/runs/usage/summary").json()
    assert summary["available"] is True and summary["calls"] == 2
