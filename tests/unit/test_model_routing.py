"""Per-role model routing (FORGE-477). Network-free.

Fake invokes stand in for providers; the real routing, provider config, pipeline
and FORGE-476 usage accounting run, and the accounting is what is asserted.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api_gateway.chat.harness_backend import run_chat_turn
from orchestrator.design_flow.generator import (
    Operation,
    OperationKind,
    apply_operations,
    parse_operations,
)
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FLOWS
from orchestrator.harness.providers import ProviderSpec
from orchestrator.harness.providers.routing import (
    RoutingConfigError,
    load_routing_table,
    resolve_route,
    routing_scope,
)
from orchestrator.harness.providers.usage import UsageStore, configure_usage_store, usage_scope

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "orchestrator/harness/providers/model_routes.example.json"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UsageStore]:
    monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(tmp_path / "auth.json"))
    # Routing is opt-in: the example file is what a deployment would copy.
    monkeypatch.setenv("METAFORGE_MODEL_ROUTES_PATH", str(EXAMPLE))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    for key in [k for k in __import__("os").environ if k.startswith("METAFORGE_ROUTE_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("METAFORGE_LLM_API_KEY", "test-key")
    store = UsageStore(":memory:")
    configure_usage_store(store)
    yield store
    configure_usage_store(None)


async def _invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
    return {
        "text": "Final Answer: ok",
        "model": spec.model,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


async def _turn(**kw: Any) -> None:
    await run_chat_turn("hello", invoke=_invoke, mcp_bridge=None, max_steps=1, **kw)


def _models(store: UsageStore, run_id: str) -> dict[str, dict[str, Any]]:
    totals = store.run_totals(run_id)
    assert totals is not None
    return totals["by_model"]


async def test_generator_and_gate_check_use_cheap_route_design_uses_strong(
    _isolated: UsageStore,
) -> None:
    with usage_scope(run_id="r1", role="flow_generator"):
        await _turn()
    with usage_scope(run_id="r1", role="gate_check"):
        await _turn()
    with (
        usage_scope(run_id="r1", phase="mech", role="phase_brain"),
        routing_scope(disciplines=("mechanical",)),
    ):
        await _turn()
    by_model = _models(_isolated, "r1")
    assert by_model["anthropic:claude-haiku-4-5-20251001"]["calls"] == 2
    assert by_model["anthropic:claude-opus-4-8"]["calls"] == 1
    roles = _isolated.run_totals("r1")["by_role"]  # type: ignore[index]
    assert set(roles) == {"flow_generator", "gate_check", "phase_brain"}


async def test_no_route_falls_back_to_durable_selection(_isolated: UsageStore) -> None:
    from orchestrator.harness.providers.auth_store import AuthStore

    AuthStore().set_selection("anthropic", "claude-sonnet-5")
    with usage_scope(run_id="r2", role="chat"):
        await _turn()
    assert list(_models(_isolated, "r2")) == ["anthropic:claude-sonnet-5"]


async def test_explicit_per_turn_model_beats_route(_isolated: UsageStore) -> None:
    with usage_scope(run_id="r3", role="flow_generator"):
        await _turn(provider="openai", model="gpt-5.5")
    assert list(_models(_isolated, "r3")) == ["openai:gpt-5.5"]


async def test_phase_model_wins_over_table_and_project(
    _isolated: UsageStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.json"
    routes.write_text(
        '{"roles": {"phase_brain": {"provider": "anthropic", "model": "claude-opus-4-8"}},'
        ' "projects": {"p1": {"roles": {"phase_brain":'
        ' {"provider": "anthropic", "model": "claude-sonnet-5"}}}}}'
    )
    monkeypatch.setenv("METAFORGE_MODEL_ROUTES_PATH", str(routes))
    with usage_scope(run_id="r4", role="phase_brain"):
        with routing_scope(project_id="p1"):
            assert resolve_route().route.model == "claude-sonnet-5"  # type: ignore[union-attr]
            await _turn()
        with routing_scope(project_id="p1", phase_model="openai:gpt-5.5"):
            assert resolve_route().source == "phase"  # type: ignore[union-attr]
            await _turn()
    assert set(_models(_isolated, "r4")) == {"anthropic:claude-sonnet-5", "openai:gpt-5.5"}


def test_mismatched_route_fails_at_load() -> None:
    with pytest.raises(RoutingConfigError, match="vendor 'anthropic'"):
        load_routing_table(
            {"roles": {"chat": {"provider": "openai-codex", "model": "claude-opus-4-8"}}}, {}
        )
    with pytest.raises(RoutingConfigError, match="unknown provider"):
        load_routing_table({"roles": {"chat": {"provider": "nope", "model": "x"}}}, {})
    with pytest.raises(RoutingConfigError, match="unknown role"):
        load_routing_table({"roles": {"wizardry": {"provider": "openai", "model": "gpt-4o"}}}, {})
    with pytest.raises(RoutingConfigError, match="projects\\['p'\\]"):
        load_routing_table(
            {"projects": {"p": {"roles": {"chat": {"provider": "anthropic", "model": "gpt-4o"}}}}},
            {},
        )


def test_env_override_is_validated_and_applied() -> None:
    table = load_routing_table(
        {"roles": {}}, {"METAFORGE_ROUTE_PHASE_BRAIN_MECHANICAL": "anthropic:claude-sonnet-5"}
    )
    assert table.roles["phase_brain:mechanical"].model == "claude-sonnet-5"
    with pytest.raises(RoutingConfigError, match="METAFORGE_ROUTE_CHAT"):
        load_routing_table({"roles": {}}, {"METAFORGE_ROUTE_CHAT": "openai-codex:claude-opus-4-8"})


def test_bad_route_via_env_refuses_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from api_gateway.server import create_app

    monkeypatch.setenv("METAFORGE_ROUTE_CHAT", "openai-codex:claude-opus-4-8")
    with pytest.raises(RoutingConfigError), TestClient(create_app()):
        pass


def test_phase_model_in_flow_is_validated_and_survives_freeze_and_templates() -> None:
    from orchestrator.design_flow.frozen import freeze_flow

    base = FLOWS[DEFAULT_FLOW_ID]
    first = base.phases[0].id
    ops = parse_operations(
        [{"op": "set_model", "phase": first, "value": "openai:gpt-5.5", "rationale": "r"}]
    )
    flow, applied = apply_operations(base, ops)
    assert [o.kind for o in applied] == [OperationKind.SET_MODEL]
    assert flow.phases[0].model == "openai:gpt-5.5"
    assert freeze_flow(flow).phases[0].model == "openai:gpt-5.5"

    bad = [Operation(OperationKind.SET_MODEL, first, "r", "openai-codex:claude-opus-4-8")]
    unchanged, applied_bad = apply_operations(base, bad)
    assert applied_bad == [] and unchanged.phases[0].model is None

    # Hand-edited flows are held to the same rule by the invariants.
    from dataclasses import replace

    edited = replace(
        base, phases=(replace(base.phases[0], model="anthropic:gpt-4o"), *base.phases[1:])
    )
    rules = {v.rule for v in validate_flow(edited).violations}
    assert "phase-model-routable" in rules


def test_unset_phase_model_keeps_frozen_hash_stable() -> None:
    import hashlib
    import json
    from dataclasses import asdict

    from orchestrator.design_flow.frozen import freeze_flow

    flow = freeze_flow(FLOWS[DEFAULT_FLOW_ID])
    # Neither ``model`` (FORGE-477), ``slots`` (FORGE-524) nor the graph fields
    # (FORGE-539) existed then.
    absent = ("model", "slots", "depends_on", "condition", "outcome")
    legacy = [{k: v for k, v in asdict(p).items() if k not in absent} for p in flow.phases]
    payload = json.dumps(legacy, sort_keys=True, separators=(",", ":"))
    # The hash a flow frozen before FORGE-477 was approved with.
    assert flow.content_hash == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    flow.verify()
    flow.phases[0].model = "openai:gpt-5.5"
    with pytest.raises(ValueError, match="does not match"):
        flow.verify()


def test_routing_endpoint_and_health(_isolated: UsageStore) -> None:
    from api_gateway.server import create_app

    # No lifespan: it would install a global MCP bridge other tests see.
    client = TestClient(create_app())
    body = client.get("/v1/harness/routing", params={"project_id": "p1"}).json()
    assert body["roles"]["flow_generator"]["model"] == "claude-haiku-4-5-20251001"
    assert body["effective_for_project"]["phase_brain:mechanical"]["model"] == "claude-opus-4-8"
    assert "chat" not in body["roles"]

    from metaforge.mcp.server import UnifiedMcpServer

    report = UnifiedMcpServer._model_routing_report()
    assert report["available"] is True and "flow_generator" in report["roles"]


def test_shipped_default_has_no_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_MODEL_ROUTES_PATH")
    assert load_routing_table().roles == {}
    assert resolve_route("flow_generator") is None


async def test_unconfigured_provider_route_is_refused_not_used_or_skipped(
    _isolated: UsageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with usage_scope(run_id="r5", role="flow_generator"):
        with pytest.raises(RoutingConfigError, match="no credentials"):
            await _turn()
    assert _isolated.run_totals("r5") is None  # no call was made on any model

    from api_gateway.server import create_app
    from metaforge.mcp.server import UnifiedMcpServer

    # No lifespan: it would install a global MCP bridge other tests see.
    client = TestClient(create_app())
    body = client.get("/v1/harness/routing").json()
    assert body["roles"]["flow_generator"]["configured"] is False
    assert any("flow_generator" in p for p in body["problems"])
    report = UnifiedMcpServer._model_routing_report()
    assert report["status"] == "degraded" and report["problems"]


def test_configured_routes_report_healthy() -> None:
    from metaforge.mcp.server import UnifiedMcpServer

    report = UnifiedMcpServer._model_routing_report()
    assert report["status"] == "ok" and report["problems"] == []
