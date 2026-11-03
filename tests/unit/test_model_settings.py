"""Per-step model settings: the gate reviewer and vision calls.

The gate reviewer runs on the project policy's ``reviewer_model``, then
``METAFORGE_REVIEWER_MODEL``, then the gateway's own model, and every review
names the model that actually reviewed. Vision calls (reading a sketch) use
``METAFORGE_VISION_MODEL`` when it is set.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals.policy import PolicyStore, reset_policy_store
from api_gateway.approvals.policy_routes import review_router
from api_gateway.approvals.policy_routes import router as policy_router
from api_gateway.approvals.routes import router as approvals_router
from api_gateway.runs import gate_review
from api_gateway.runs import routes as run_routes
from api_gateway.twin import vision
from api_gateway.twin.sketch_reading import (
    SketchVisionUnavailableError,
    VisionReply,
    make_default_vision_call,
)
from orchestrator.harness.providers import ProviderSpec, RetryPolicy, RoleModelSlots
from orchestrator.harness.providers.provenance import ServedBy, note_served

PROJECT = "p1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

PAYLOAD = {
    "run_id": "run_1",
    "project_id": PROJECT,
    "goal": "a chair",
    "phase": {"id": "concept_selection", "title": "Concept", "objective": "o"},
    "gate": "G5",
    "attempt": 1,
    "ready": True,
    "findings": [],
}


# ── the reviewer's model: policy, then env, then the gateway default ───────


def test_the_policy_model_wins_over_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_REVIEWER_MODEL", "openrouter:anthropic/claude-sonnet-4.5")
    assert gate_review.reviewer_model({"reviewer_model": " anthropic:claude-opus-4-8 "}) == (
        "anthropic:claude-opus-4-8",
        "policy",
    )


def test_the_env_model_is_used_when_the_policy_names_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METAFORGE_REVIEWER_MODEL", "anthropic/claude-sonnet-4.5")
    assert gate_review.reviewer_model({"reviewer_model": ""}) == (
        "anthropic/claude-sonnet-4.5",
        "env",
    )


def test_neither_set_falls_back_to_the_gateway_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_REVIEWER_MODEL", raising=False)
    assert gate_review.reviewer_model({}) == ("", "default")
    assert gate_review.reviewer_model(None) == ("", "default")


def _fake_gateway(posted: list[dict], *, reviewer_model: str) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/approval-policy"):
            policy = {"mode": "advise", "reviewer": "server", "reviewer_model": reviewer_model}
            return httpx.Response(200, json={"policy": policy, "effective_mode": "advise"})
        if path.endswith("/gate-review"):
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"applied": "advised"})
        if path.startswith("/v1/runs/"):
            return httpx.Response(200, json={"request": {"decisions": []}})
        return httpx.Response(200, json={"nodes": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _capture_server_review(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> list[str]:
    asked: list[str] = []

    async def fake(brief: dict[str, Any], *, model_ref: str) -> dict[str, Any]:
        asked.append(model_ref)
        return dict(result)

    monkeypatch.setattr(gate_review, "server_review", fake)
    return asked


def test_the_review_step_runs_the_env_model_when_the_policy_names_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METAFORGE_REVIEWER_MODEL", "openrouter:anthropic/claude-sonnet-4.5")
    asked = _capture_server_review(
        monkeypatch,
        {
            "recommendation": "approve",
            "confidence": 0.9,
            "model": "openrouter:anthropic/claude-sonnet-4.5",
        },
    )
    posted: list[dict] = []
    asyncio.run(gate_review.review_gate(PAYLOAD, client=_fake_gateway(posted, reviewer_model="")))
    assert asked == ["openrouter:anthropic/claude-sonnet-4.5"]
    assert posted[0]["model"] == "openrouter:anthropic/claude-sonnet-4.5"
    assert posted[0]["model_source"] == "env"


def test_the_review_step_prefers_the_policy_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_REVIEWER_MODEL", "openrouter:anthropic/claude-sonnet-4.5")
    asked = _capture_server_review(monkeypatch, {"recommendation": "approve"})
    posted: list[dict] = []
    gw = _fake_gateway(posted, reviewer_model="anthropic:claude-opus-4-8")
    asyncio.run(gate_review.review_gate(PAYLOAD, client=gw))
    assert asked == ["anthropic:claude-opus-4-8"]
    assert posted[0]["model"] == "anthropic:claude-opus-4-8"
    assert posted[0]["model_source"] == "policy"


def test_a_default_review_names_the_model_the_gateway_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The sandbox case: no policy model, no env, the gateway default ran. The
    # review used to say "default"; it now names the model.
    monkeypatch.delenv("METAFORGE_REVIEWER_MODEL", raising=False)
    monkeypatch.setattr(gate_review, "_resolved_model", lambda ref: "openrouter:openai/gpt-4o")
    asked = _capture_server_review(monkeypatch, {"recommendation": "retry"})
    posted: list[dict] = []
    asyncio.run(gate_review.review_gate(PAYLOAD, client=_fake_gateway(posted, reviewer_model="")))
    assert asked == [""]
    assert posted[0]["model"] == "openrouter:openai/gpt-4o"
    assert posted[0]["model_source"] == "default"


def test_a_failed_default_review_still_names_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_REVIEWER_MODEL", raising=False)
    monkeypatch.setattr(gate_review, "_resolved_model", lambda ref: "openrouter:openai/gpt-4o")

    async def broken(brief: dict[str, Any], *, model_ref: str) -> dict[str, Any]:
        raise RuntimeError("provider down")

    monkeypatch.setattr(gate_review, "server_review", broken)
    posted: list[dict] = []
    asyncio.run(gate_review.review_gate(PAYLOAD, client=_fake_gateway(posted, reviewer_model="")))
    assert posted[0]["recommendation"] == "none"
    assert posted[0]["model"] == "openrouter:openai/gpt-4o"


def test_server_review_reports_the_model_that_served_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_gateway.chat.harness_backend as backend
    import api_gateway.chat.routes as chat_routes

    async def fake_turn(prompt: str, **kw: Any) -> str:
        # What the provider pipeline does for the response that answered.
        note_served(ServedBy(provider="openrouter", model="openai/gpt-4o", role="gate_check"))
        return '{"recommendation": "approve", "confidence": 0.8}'

    monkeypatch.setattr(backend, "run_chat_turn", fake_turn)
    monkeypatch.setattr(chat_routes, "get_mcp_bridge", lambda: None)
    monkeypatch.setattr(chat_routes, "get_metrics", lambda: None)
    review = asyncio.run(gate_review.server_review({"run_id": "r", "phase": {}}, model_ref=""))
    assert review["recommendation"] == "approve"
    assert review["model"] == "openrouter:openai/gpt-4o"


# ── the stored review names the model ──────────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    run_routes.reset_run_store()
    reset_policy_store(PolicyStore())
    app = FastAPI()
    app.include_router(approvals_router)
    app.include_router(policy_router)
    app.include_router(review_router)
    yield TestClient(app)
    run_routes.reset_run_store()
    reset_policy_store(None)


def test_the_recorded_review_names_the_model_and_where_it_came_from(
    client: TestClient,
) -> None:
    client.put(f"/v1/projects/{PROJECT}/approval-policy", json={"mode": "advise"})
    store = run_routes.get_run_store()
    run = store.create({"flow": "design_v1", "flow_engine": "in_process", "project_id": PROJECT})
    store.start(run.id)
    run_routes.get_gate_coordinator().set_gate_state(
        run.id, ready=True, retries_left=2, phase="design", reworks_left=1
    )
    store.request_approval(run.id, reason="")
    stored = client.post(
        f"/v1/runs/{run.id}/gate-review",
        json={
            "phase": "design",
            "recommendation": "approve",
            "confidence": 0.9,
            "model": "openrouter:openai/gpt-4o",
            "model_source": "default",
        },
    ).json()
    assert stored["model"] == "openrouter:openai/gpt-4o"
    assert stored["model_source"] == "default"
    assert stored["reviewer"] == "reviewer:server:openrouter:openai/gpt-4o"
    item = client.get(f"/v1/approvals/gate:{run.id}").json()
    assert item["review"]["model"] == "openrouter:openai/gpt-4o"


# ── the vision model ────────────────────────────────────────────────────────


class _Config:
    def __init__(self, specs: list[ProviderSpec]) -> None:
        self.slots = RoleModelSlots(slots={"generator": specs})
        self.retry = RetryPolicy(api_max_retries=0)


def test_vision_model_reads_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_VISION_MODEL", raising=False)
    assert vision.vision_model() is None
    monkeypatch.setenv("METAFORGE_VISION_MODEL", "  ")
    assert vision.vision_model() is None
    monkeypatch.setenv("METAFORGE_VISION_MODEL", " anthropic/claude-sonnet-4.5 ")
    assert vision.vision_model() == "anthropic/claude-sonnet-4.5"


@pytest.mark.parametrize("env", [None, "google/gemini-2.5-pro"])
def test_the_vision_chain_carries_the_vision_model_when_set(
    monkeypatch: pytest.MonkeyPatch, env: str | None
) -> None:
    import api_gateway.chat.harness_backend as backend

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        backend, "provider_config_from_env", lambda **kw: seen.append(kw) or "config"
    )
    if env is None:
        monkeypatch.delenv("METAFORGE_VISION_MODEL", raising=False)
    else:
        monkeypatch.setenv("METAFORGE_VISION_MODEL", env)
    assert vision.vision_provider_config() == "config"
    assert seen == ([{}] if env is None else [{"model": env}])


def test_reading_a_sketch_runs_on_the_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.chat.harness_backend as backend

    monkeypatch.setenv("METAFORGE_VISION_MODEL", "anthropic/claude-sonnet-4.5")

    def chain(*, model: str | None = None, **_: Any) -> _Config:
        return _Config([ProviderSpec(name="openrouter", model=model or "openai/gpt-4o")])

    monkeypatch.setattr(backend, "provider_config_from_env", chain)
    sent: list[str] = []

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        sent.append(spec.model)
        return {"text": "{}"}

    reply = asyncio.run(make_default_vision_call(invoke=invoke)(PNG, "image/png", "read it"))
    assert isinstance(reply, VisionReply)
    assert sent == ["anthropic/claude-sonnet-4.5"]
    assert (reply.provider, reply.model) == ("openrouter", "anthropic/claude-sonnet-4.5")


def test_the_shared_vision_call_raises_the_callers_error() -> None:
    call = vision.make_vision_call(
        provider_config=_Config([ProviderSpec(name="gemini", model="g")]),
        unavailable=SketchVisionUnavailableError,
    )
    with pytest.raises(SketchVisionUnavailableError, match="no vision-capable"):
        asyncio.run(call(PNG, "image/png", "look"))
    plain = vision.make_vision_call(
        provider_config=_Config([ProviderSpec(name="gemini", model="g")])
    )
    with pytest.raises(vision.VisionUnavailableError):
        asyncio.run(plain(PNG, "image/png", "look"))
