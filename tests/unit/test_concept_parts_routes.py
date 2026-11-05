"""The concept parts routes: paid steps as jobs with the spend rules, a person's mapping.

Nothing calls Tripo: the service runs on the fake of ``test_concept_parts``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from api_gateway.approvals import policy as approval_policy
from api_gateway.approvals.policy import PolicyStore
from api_gateway.operations.routes import publish_local
from api_gateway.twin import concept_parts_routes as routes
from api_gateway.twin import routes as twin_routes
from tests.unit.test_concept_parts import PROJECT, World
from tool_registry.jobs import JobRunner, JobStore

pytestmark = pytest.mark.asyncio

BASE = f"/v1/twin/projects/{PROJECT}/concept-parts"


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    w = World()
    monkeypatch.setattr(twin_routes, "_twin", w.twin)
    monkeypatch.setattr(approval_policy, "_store", PolicyStore())
    w.runner = JobRunner(JobStore(":memory:"), publisher=publish_local)  # type: ignore[attr-defined]
    routes.set_service(w.service)
    routes.set_job_runner(w.runner)  # type: ignore[attr-defined]
    yield w
    routes.set_service(None)
    routes.set_job_runner(None)


@pytest.fixture
async def client(world: World) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _finish(world: World, body: dict[str, Any]) -> dict[str, Any]:
    job = await world.runner.wait(body["job_id"], 5)  # type: ignore[attr-defined]
    assert job is not None and job.status == "succeeded", job and job.error
    return dict(job.result or {})


async def test_segment_runs_as_a_job_and_records_who_asked(
    world: World, client: httpx.AsyncClient
) -> None:
    original = await world.lamp()
    state = (await client.get(BASE)).json()
    assert state["current"]["node_id"] == original and state["proposals"] == []
    started = await client.post(f"{BASE}/segment", json={})
    assert started.status_code == 200, started.text
    body = started.json()
    assert (
        body["tool"] == "twin.segment_concept_mesh"
        and body["operation_id"] == f"job:{body['job_id']}"
    )
    result = await _finish(world, body)
    meta = (await world.node(result["node_id"])).metadata
    assert meta["request"]["surface"] == "create" and meta["request"]["approved_by"].startswith(
        "person:"
    )
    state = (await client.get(BASE)).json()
    # The accepted concept stays on stage; the split is a proposal beside it.
    assert state["current"]["node_id"] == original
    assert [p["node_id"] for p in state["proposals"]] == [result["node_id"]]
    assert {c["name"] for c in state["components"]} == {"Base", "Arm", "Shade"}


async def test_agents_are_refused_and_spend_past_the_cap_is_asked(
    world: World, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await world.lamp()
    refused = await client.post(
        f"{BASE}/segment", json={}, headers={"X-MetaForge-Surface": "agent"}
    )
    assert refused.status_code == 403
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", "1")
    # The original concept mesh was made this hour: the cap of 1 is reached.
    asked = await client.post(f"{BASE}/segment", json={})
    assert asked.status_code == 409
    assert asked.json()["detail"]["code"] == "spend_confirmation_required"
    confirmed = await client.post(f"{BASE}/segment", json={"confirm_spend": True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["spend"]["used_last_hour"] == 1


async def test_mapping_regenerate_and_accept_through_the_routes(
    world: World, client: httpx.AsyncClient
) -> None:
    await world.lamp()
    seg = await _finish(world, (await client.post(f"{BASE}/segment", json={})).json())
    node = seg["node_id"]
    agent = await client.post(
        f"{BASE}/{node}/mapping",
        json={"accept_proposals": True},
        headers={"X-MetaForge-Surface": "agent"},
    )
    assert agent.status_code == 403
    mapped = await client.post(f"{BASE}/{node}/mapping", json={"accept_proposals": True})
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["mapping"]["status"] == "confirmed"
    comps = {c["name"]: c["component_id"] for c in (await client.get(BASE)).json()["components"]}

    bad = await client.post(
        f"{BASE}/{node}/regenerate", json={"component_id": comps["Shade"], "source": "text"}
    )
    assert bad.status_code == 422 and "prompt" in bad.json()["detail"]
    redo = await client.post(
        f"{BASE}/{node}/regenerate",
        json={
            "component_id": comps["Shade"],
            "source": "text",
            "prompt": "a dome",
            "confirm_spend": True,
        },
    )
    assert redo.status_code == 200, redo.text
    redone = await _finish(world, redo.json())
    assert redone["concept_status"] == "proposed" and redone["operation"] == "regenerated_part"

    seal = await client.post(
        f"{BASE}/{node}/complete",
        json={"component_id": comps["Base"], "completion_mode": "quick_cap", "confirm_spend": True},
    )
    assert seal.status_code == 200, seal.text
    await _finish(world, seal.json())
    assert world.tripo.calls[-1][1]["part_names"] == ["tripo_part_0"]

    accepted = await client.post(
        f"{BASE}/{redone['node_id']}/decision", json={"decision": "accept"}
    )
    assert accepted.status_code == 200, accepted.text
    assert (await client.get(BASE)).json()["current"]["node_id"] == redone["node_id"]
    missing = await client.post(
        f"{BASE}/00000000-0000-0000-0000-000000000000/decision", json={"decision": "accept"}
    )
    assert missing.status_code == 404
