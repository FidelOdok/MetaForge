"""Approval policy and the gate reviewer (FORGE-725..729).

A reviewer agent reads each design-flow gate and recommends a decision. The
project's approval policy says what that recommendation does: nothing in
``off``, shown to the person in ``advise``, and acted on in ``auto_clean`` and
``auto`` under strict conditions. These tests drive the gateway side with the
in-process engine's gate, and the review step with a fake gateway.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals import service
from api_gateway.approvals.policy import (
    ApprovalPolicy,
    PolicyStore,
    effective_mode,
    reset_policy_store,
)
from api_gateway.approvals.policy_routes import review_router
from api_gateway.approvals.policy_routes import router as policy_router
from api_gateway.approvals.routes import router as approvals_router
from api_gateway.runs import routes as run_routes
from api_gateway.runs.gate_review import parse_review, review_gate, review_skill
from mcp_core.annotations import ADDITIVE, DESTRUCTIVE
from mcp_core.guardrails import BOOKKEEPING
from mcp_core.profiles import PROFILES, REVIEW_TOOLS
from orchestrator.design_flow.temporal_activities import DesignFlowActivities

PROJECT = "p1"


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


def _gate(*, ready: bool = True, phase: str = "design") -> str:
    store = run_routes.get_run_store()
    run = store.create({"flow": "design_v1", "flow_engine": "in_process", "project_id": PROJECT})
    store.start(run.id)
    run_routes.get_gate_coordinator().set_gate_state(
        run.id, ready=ready, retries_left=2, phase=phase, reworks_left=1
    )
    store.request_approval(
        run.id,
        reason="" if ready else "[design gate] NOT READY (retry the phase or reject): missing",
    )
    return run.id


def _set(client: TestClient, mode: str, **kw: Any) -> Any:
    return client.put(f"/v1/projects/{PROJECT}/approval-policy", json={"mode": mode, **kw})


def _review(client: TestClient, run_id: str, **kw: Any) -> Any:
    body = {"phase": "design", "recommendation": "approve", "confidence": 0.9, "model": "m"} | kw
    return client.post(f"/v1/runs/{run_id}/gate-review", json=body)


# ── the policy ──────────────────────────────────────────────────────────────


def test_the_default_is_off_and_the_cap_is_advise(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = client.get(f"/v1/projects/{PROJECT}/approval-policy").json()
    assert view["policy"]["mode"] == "off" and view["effective_mode"] == "off"
    monkeypatch.delenv("METAFORGE_AUTO_APPROVE_MAX")
    assert _set(client, "auto_clean").status_code == 422  # above the default cap
    assert _set(client, "advise").status_code == 200


def test_an_agent_cannot_change_the_policy(client: TestClient) -> None:
    resp = client.put(
        f"/v1/projects/{PROJECT}/approval-policy",
        json={"mode": "auto"},
        headers={"X-MetaForge-Surface": "agent"},
    )
    assert resp.status_code == 403


def test_auto_expires_back_to_advise() -> None:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    policy = ApprovalPolicy(project_id=PROJECT, mode="auto", expires_at=past)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
        assert effective_mode(policy) == "advise"


def test_auto_gets_an_expiry(client: TestClient) -> None:
    view = _set(client, "auto", expires_in_hours=2).json()
    ends = datetime.fromisoformat(view["policy"]["expires_at"])
    assert timedelta(hours=1.9) < ends - datetime.now(UTC) <= timedelta(hours=2)
    assert view["effective_mode"] == "auto"


# ── advise: shown on the gate, never decides ───────────────────────────────


def test_advise_shows_the_review_on_the_gate_and_decides_nothing(client: TestClient) -> None:
    _set(client, "advise")
    run_id = _gate()
    stored = _review(client, run_id, recommendation="retry", reasons="No material.").json()
    assert stored["applied"] == "advised"
    item = client.get(f"/v1/approvals/gate:{run_id}").json()
    assert item["status"] == "pending"
    assert item["review"]["recommendation"] == "retry"
    assert item["review"]["reviewer"] == "reviewer:server:m"


def test_a_review_of_another_phase_is_not_shown(client: TestClient) -> None:
    _set(client, "advise")
    run_id = _gate(phase="design")
    _review(client, run_id, phase="requirements")
    assert client.get(f"/v1/approvals/gate:{run_id}").json()["review"] is None


def test_a_persons_decision_is_compared_with_the_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, bool]] = []

    class _M:
        def record_gate_review(self, *a: Any) -> None: ...

        def record_gate_review_agreement(self, rec: str, dec: str, agreed: bool) -> None:
            seen.append((rec, dec, agreed))

    monkeypatch.setattr(service, "_review_metrics", _M())
    _set(client, "advise")
    run_id = _gate()
    _review(client, run_id, recommendation="approve")
    resp = client.post(f"/v1/approvals/gate:{run_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 200, resp.text
    assert seen == [("approve", "approve", True)]


# ── auto_clean and auto ────────────────────────────────────────────────────


def test_auto_clean_approves_a_clean_gate_as_the_reviewer(client: TestClient) -> None:
    _set(client, "auto_clean")
    run_id = _gate(ready=True)
    stored = _review(client, run_id, recommendation="approve", confidence=0.95).json()
    assert stored["applied"] == "decided"
    record = client.get(f"/v1/approvals/gate:{run_id}").json()["decision"]
    assert record["approver"] == "reviewer:server:m"
    assert record["surface"] == "reviewer"


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"recommendation": "approve", "confidence": 0.5}, "confidence"),
        ({"recommendation": "retry", "reasons": "x"}, "only approves"),
    ],
)
def test_auto_clean_holds_anything_doubtful(client: TestClient, kw: dict, why: str) -> None:
    _set(client, "auto_clean")
    run_id = _gate(ready=True)
    stored = _review(client, run_id, **kw).json()
    assert stored["applied"] == "held" and why in stored["applied_note"]
    assert client.get(f"/v1/approvals/gate:{run_id}").json()["status"] == "pending"


def test_auto_clean_never_approves_a_gate_whose_checks_failed(client: TestClient) -> None:
    _set(client, "auto_clean")
    run_id = _gate(ready=False)
    stored = _review(client, run_id, recommendation="approve", confidence=0.99).json()
    assert stored["applied"] == "held"
    assert client.get(f"/v1/approvals/gate:{run_id}").json()["status"] == "pending"


def test_auto_retries_with_the_reviewers_reasons(client: TestClient) -> None:
    _set(client, "auto")
    run_id = _gate(ready=False)
    stored = _review(
        client, run_id, recommendation="retry", confidence=0.7, reasons="Record a material."
    ).json()
    assert stored["applied"] == "decided"
    record = client.get(f"/v1/approvals/gate:{run_id}").json()["decision"]
    assert record["decision"] == "retry" and "Record a material." in record["reason"]


def test_the_reviewer_never_rejects_on_its_own(client: TestClient) -> None:
    _set(client, "auto")
    run_id = _gate()
    stored = _review(client, run_id, recommendation="reject", confidence=1.0, reasons="x").json()
    assert stored["applied"] == "held"
    assert client.get(f"/v1/approvals/gate:{run_id}").json()["status"] == "pending"


# ── the review step ─────────────────────────────────────────────────────────


def _fake_gateway(mode: str, posted: list[dict], *, reviewer: str = "server") -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/approval-policy"):
            policy = {"mode": mode, "reviewer": reviewer, "reviewer_model": "anthropic:opus"}
            return httpx.Response(200, json={"policy": policy, "effective_mode": mode})
        if path.endswith("/gate-review"):
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"applied": "advised"})
        if path.startswith("/v1/runs/"):
            return httpx.Response(200, json={"request": {"decisions": []}})
        return httpx.Response(200, json={"nodes": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


PAYLOAD = {
    "run_id": "run_1",
    "project_id": PROJECT,
    "goal": "a chair",
    "phase": {"id": "design", "title": "Design", "objective": "o", "required_deliverables": []},
    "gate": "G6",
    "attempt": 2,
    "ready": False,
    "findings": ["Chair Seat records no material"],
}


def test_the_review_step_posts_the_reviewers_view() -> None:
    posted: list[dict] = []
    briefs: list[dict] = []

    async def reviewer(brief: dict) -> dict:
        briefs.append(brief)
        return {"recommendation": "retry", "reasons": "r", "concerns": [], "confidence": 0.8}

    out = asyncio.run(
        review_gate(PAYLOAD, client=_fake_gateway("advise", posted), reviewer=reviewer)
    )
    assert out["status"] == "reviewed"
    assert posted[0]["recommendation"] == "retry" and posted[0]["attempt"] == 2
    assert posted[0]["model"] == "anthropic:opus" and posted[0]["source"] == "server"
    assert briefs[0]["checks"]["findings"] == ["Chair Seat records no material"]


def test_no_review_when_the_policy_is_off_or_the_client_reviews() -> None:
    posted: list[dict] = []

    async def reviewer(brief: dict) -> dict:  # pragma: no cover - must not run
        raise AssertionError("reviewed while off")

    off = asyncio.run(review_gate(PAYLOAD, client=_fake_gateway("off", posted), reviewer=reviewer))
    client = asyncio.run(
        review_gate(
            PAYLOAD, client=_fake_gateway("advise", posted, reviewer="client"), reviewer=reviewer
        )
    )
    assert off["status"] == client["status"] == "skipped" and posted == []


def test_a_failing_reviewer_still_says_so_on_the_gate() -> None:
    posted: list[dict] = []

    async def reviewer(brief: dict) -> dict:
        raise RuntimeError("model unavailable")

    asyncio.run(review_gate(PAYLOAD, client=_fake_gateway("advise", posted), reviewer=reviewer))
    assert posted[0]["recommendation"] == "none" and "model unavailable" in posted[0]["error"]


def test_the_activity_never_raises() -> None:
    async def boom(payload: dict) -> dict:
        raise RuntimeError("down")

    acts = DesignFlowActivities(phase_runner=None, gate_reviewer=boom)  # type: ignore[arg-type]

    async def go() -> dict:
        return await acts.review_gate.__wrapped__(acts, PAYLOAD)  # type: ignore[attr-defined]

    try:
        out = asyncio.run(go())
    except AttributeError:  # temporalio keeps the function itself
        out = asyncio.run(DesignFlowActivities.review_gate(acts, PAYLOAD))
    assert out["status"] == "failed"


@pytest.mark.parametrize(
    ("reply", "rec"),
    [
        ('Looks fine.\n```json\n{"recommendation": "approve", "confidence": 0.9}\n```', "approve"),
        ('{"recommendation": "Retry", "reasons": "x", "confidence": "0.4"}', "retry"),
        ('{"recommendation": "ship it"}', "none"),
    ],
)
def test_the_reviewers_answer_is_parsed(reply: str, rec: str) -> None:
    assert parse_review(reply)["recommendation"] == rec


def test_a_reply_without_a_review_is_an_error() -> None:
    with pytest.raises(ValueError):
        parse_review("I think it is fine.")


# ── the reviewer's ecosystem ────────────────────────────────────────────────


def test_the_review_profile_cannot_write() -> None:
    assert PROFILES["review"] is REVIEW_TOOLS
    # Only its own session record (bookkeeping) may write; no design state.
    assert not ((REVIEW_TOOLS - BOOKKEEPING) & (ADDITIVE | DESTRUCTIVE))
    assert not any(".record_" in t or t.startswith("twin.commit") for t in REVIEW_TOOLS)


def test_the_review_skill_carries_the_answer_contract() -> None:
    skill = review_skill()
    assert "Substance, not presence" in skill
    assert '"recommendation"' in skill and '"confidence"' in skill
