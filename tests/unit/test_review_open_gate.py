"""A gate that opened without a review is reviewed later (FORGE-743).

A review ran only when a gate opened. The Chair run's DFM gate opened while
the project's policy was ``off``; the policy was then set to ``auto`` and the
gate still waited for a person, because nothing ever asked for its review.
Now the supervisor asks on its next tick, and so does setting the policy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from api_gateway.approvals.policy_routes import router as policy_router
from api_gateway.runs import gate_review, supervisor
from api_gateway.runs import routes as run_routes

PROJECT = "p-chair"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str, Any]]]:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    run_routes.reset_run_store()
    reset_policy_store(PolicyStore())
    asked: list[tuple[str, str, Any]] = []

    def fake_request(run_id: str, phase: str, attempt: Any) -> bool:
        asked.append((run_id, phase, attempt))
        return True

    monkeypatch.setattr(gate_review, "request_open_gate_review", fake_request)
    monkeypatch.setattr(supervisor, "REVIEW_GRACE_SECONDS", 0.0)
    yield asked
    run_routes.reset_run_store()
    reset_policy_store(None)


def _policy(mode: str) -> None:
    from api_gateway.approvals.policy import get_policy_store

    asyncio.run(get_policy_store().put(ApprovalPolicy(project_id=PROJECT, mode=mode)))


def _run_at_gate() -> str:
    store = run_routes.get_run_store()
    run = store.create(
        {
            "kind": "design_flow",
            "flow": "mech_v1",
            "goal": "a chair",
            "project_id": PROJECT,
            "flow_engine": "in_process",
        }
    )
    store.start(run.id)
    run_routes.get_gate_coordinator().set_gate_state(
        run.id, ready=False, retries_left=2, phase="dfm_review", reworks_left=2
    )
    store.request_approval(run.id, reason="[G4] NOT READY: no joining features")
    return run.id


def _tick(run_id: str) -> dict[str, Any]:
    return asyncio.run(supervisor.supervise_tick(run_id)) or {}


def test_the_supervisor_asks_for_a_review_of_an_unreviewed_gate(
    _fresh: list[tuple[str, str, Any]],
) -> None:
    _policy("auto")
    run_id = _run_at_gate()
    supervisor.start_supervision(run_id)
    sup = _tick(run_id)
    assert [a[:2] for a in _fresh] == [(run_id, "dfm_review")]
    assert "review" in [e["action"] for e in sup["log"]]
    # Asked once per gate, not on every tick.
    _tick(run_id)
    assert len(_fresh) == 1


def test_nothing_is_asked_while_the_policy_is_off(_fresh: list[tuple[str, str, Any]]) -> None:
    _policy("off")
    run_id = _run_at_gate()
    supervisor.start_supervision(run_id)
    _tick(run_id)
    assert _fresh == []


def test_setting_the_policy_reviews_the_gates_already_waiting(
    _fresh: list[tuple[str, str, Any]],
) -> None:
    run_id = _run_at_gate()
    app = FastAPI()
    app.include_router(policy_router)
    client = TestClient(app)
    response = client.put(
        f"/v1/projects/{PROJECT}/approval-policy",
        json={"mode": "auto", "reviewer": "server"},
        headers={"X-MetaForge-Surface": "dashboard"},
    )
    assert response.status_code == 200, response.text
    assert [a[:2] for a in _fresh] == [(run_id, "dfm_review")]


def test_turning_the_policy_off_asks_for_nothing(_fresh: list[tuple[str, str, Any]]) -> None:
    _run_at_gate()
    app = FastAPI()
    app.include_router(policy_router)
    TestClient(app).put(
        f"/v1/projects/{PROJECT}/approval-policy",
        json={"mode": "off"},
        headers={"X-MetaForge-Surface": "dashboard"},
    )
    assert _fresh == []


def test_the_review_is_of_the_open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_review(payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        seen.append(payload)
        return {"status": "reviewed"}

    monkeypatch.setattr(gate_review, "review_gate", fake_review)
    run_id = _run_at_gate()
    outcome = asyncio.run(gate_review.review_open_gate(run_id))
    assert outcome == {"status": "reviewed"}
    payload = seen[0]
    assert payload["run_id"] == run_id and payload["project_id"] == PROJECT
    assert payload["phase"]["id"] == "dfm_review"
    assert payload["ready"] is False
    assert payload["findings"]


def test_a_gate_that_already_has_a_review_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("must not review twice")

    monkeypatch.setattr(gate_review, "review_gate", fail)
    run_id = _run_at_gate()
    run_routes.get_run_store().annotate(
        run_id,
        "gate_reviews",
        [{"phase": "dfm_review", "recommendation": "rework", "confidence": 0.8}],
    )
    outcome = asyncio.run(gate_review.review_open_gate(run_id))
    assert outcome["status"] == "skipped"


def test_a_gate_just_opened_is_left_to_the_workers_review(
    _fresh: list[tuple[str, str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # FORGE-751: the worker reviews a gate as it opens; asking at once made
    # two reviews of the Chair design gate 7 s apart.
    monkeypatch.setattr(supervisor, "REVIEW_GRACE_SECONDS", 180.0)
    _policy("auto")
    run_id = _run_at_gate()
    supervisor.start_supervision(run_id)
    _tick(run_id)
    assert _fresh == []
    sup = supervisor.supervision_of(run_routes.get_run_store().get(run_id))
    sup["gate_first_seen"] -= 200
    run_routes.get_run_store().annotate(run_id, "supervision", sup)
    _tick(run_id)
    assert [a[:2] for a in _fresh] == [(run_id, "dfm_review")]
