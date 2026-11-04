"""The Create view's records: brief, readiness, builds, inspection, acceptance."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.create import routes as create_routes
from api_gateway.create.models import Creation, InspectionCheck, Measurement
from api_gateway.create.readiness import WorkProductSummary, derive
from api_gateway.create.store import CreationConflictError, SqliteCreationStore
from api_gateway.projects import routes as project_routes
from api_gateway.projects.backend import InMemoryProjectBackend

PERSON = {"X-MetaForge-Surface": "dashboard"}
AGENT = {"X-MetaForge-Surface": "agent"}


@pytest.fixture
def backend() -> Iterator[InMemoryProjectBackend]:
    previous = project_routes.get_project_backend()
    fresh = InMemoryProjectBackend.create()
    project_routes.init_project_backend(fresh)
    yield fresh
    project_routes.init_project_backend(previous)


@pytest.fixture
def client(tmp_path: Path, backend: InMemoryProjectBackend) -> Iterator[TestClient]:
    create_routes.init_create_store(SqliteCreationStore(str(tmp_path / "create.db")))
    app = FastAPI()
    app.include_router(create_routes.router)
    with TestClient(app) as c:
        yield c
    create_routes.init_create_store(None)


def _project(backend: InMemoryProjectBackend, *, model: bool = True) -> str:
    async def make() -> str:
        p = await backend.create_project(name="Desk hook", description="A hook", status="draft")
        if model:
            await backend.link_work_product(p.id, "wp-model", "Hook body", "cad_model")
        return p.id

    return asyncio.run(make())


def _plan() -> list[dict[str, Any]]:
    return [
        {
            "id": "fit",
            "feature": "Fits the desk edge",
            "condition": "slides on by hand",
            "method": "operator",
        },
        {"id": "look", "feature": "Surface finish", "method": "camera", "blocking": False},
    ]


def _ready(client: TestClient, pid: str) -> dict[str, Any]:
    client.put(f"/v1/create/{pid}/route", json={"route": {"route": "home", "process": "fdm"}})
    client.put(f"/v1/create/{pid}/inspection-plan", json={"checks": _plan()})
    r = client.post(
        f"/v1/create/{pid}/builds",
        json={"revision": {"work_product_id": "wp-model", "version": 2, "label": "Rev B"}},
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestReadiness:
    def test_an_image_is_a_concept_not_a_model(self) -> None:
        r = derive(Creation(project_id="p"), [WorkProductSummary(id="i", type="image")])
        assert r.stage == "concept"
        assert r.has_concept and not r.has_engineering_model
        assert "concepts" in r.blockers[0].message

    def test_a_model_with_a_decision_waiting_is_still_in_development(self) -> None:
        wps = [WorkProductSummary(id="m", type="cad_model", status="draft")]
        assert (
            derive(Creation(project_id="p"), wps, pending_approvals=2).stage
            == "design_in_development"
        )
        assert derive(Creation(project_id="p"), wps).stage == "checked_design"

    def test_a_failing_check_holds_it_in_development(self) -> None:
        wps = [WorkProductSummary(id="m", name="Body", type="cad_model", status="invalid")]
        r = derive(Creation(project_id="p"), wps)
        assert r.stage == "design_in_development"
        assert r.blockers[0].id == "wp:m"

    def test_an_unconfirmed_critical_dimension_blocks_making(self) -> None:
        c = Creation(project_id="p")
        c.route.route = "home"
        c.inspection_plan = [InspectionCheck(id="a", feature="fit")]
        c.brief.measurements = [
            Measurement(id="w", label="Desk thickness", value=25, critical=True)
        ]
        r = derive(c, [WorkProductSummary(id="m", type="cad_model")])
        assert r.stage == "checked_design"
        assert [b.id for b in r.blockers] == ["measurement:w"]


class TestStore:
    def test_a_stale_write_is_a_conflict(self, tmp_path: Path) -> None:
        store = SqliteCreationStore(str(tmp_path / "c.db"))
        first = store.save(Creation(project_id="p"), expected_version=0)
        assert first.version == 1
        with pytest.raises(CreationConflictError) as exc:
            store.save(Creation(project_id="p"), expected_version=0)
        assert exc.value.current.version == 1


class TestRoutes:
    def test_a_new_project_reads_as_an_empty_concept(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend, model=False)
        body = client.get(f"/v1/create/{pid}").json()
        assert body["creation"]["version"] == 0
        assert body["readiness"]["stage"] == "concept"
        assert body["brief_keys"][0] == "intended_use"

    def test_unknown_project_is_404(self, client: TestClient) -> None:
        assert client.get("/v1/create/nope").status_code == 404

    def test_brief_edit_from_an_older_version_is_refused(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend)
        brief = {"items": {"intended_use": {"value": "Hang headphones", "status": "confirmed"}}}
        ok = client.put(f"/v1/create/{pid}/brief", json={"brief": brief, "expected_version": 0})
        assert ok.json()["creation"]["version"] == 1
        stale = client.put(f"/v1/create/{pid}/brief", json={"brief": brief, "expected_version": 0})
        assert stale.status_code == 409
        assert stale.json()["detail"]["current"]["version"] == 1

    def test_unknown_brief_item_is_422(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend)
        r = client.put(
            f"/v1/create/{pid}/brief", json={"brief": {"items": {"colour": {"value": "red"}}}}
        )
        assert r.status_code == 422

    def test_the_whole_loop(self, client: TestClient, backend: InMemoryProjectBackend) -> None:
        pid = _project(backend)
        view = _ready(client, pid)
        assert view["readiness"]["stage"] == "ready_for_making"
        build = view["creation"]["builds"][0]
        bid = build["id"]
        assert build["revision"]["label"] == "Rev B"

        # An agent can neither release nor accept.
        assert (
            client.post(
                f"/v1/create/{pid}/builds/{bid}/release", json={}, headers=AGENT
            ).status_code
            == 403
        )
        released = client.post(
            f"/v1/create/{pid}/builds/{bid}/release", json={}, headers=PERSON
        ).json()
        assert released["readiness"]["stage"] == "released_for_making"
        assert released["creation"]["builds"][0]["results"]["fit"]["status"] == "awaiting_review"

        # A released build keeps its instructions.
        edit = client.patch(f"/v1/create/{pid}/builds/{bid}", json={"material": "PETG"})
        assert edit.status_code == 409

        client.post(f"/v1/create/{pid}/builds/{bid}/status", json={"status": "making"})
        done = client.post(
            f"/v1/create/{pid}/builds/{bid}/status", json={"status": "completed"}
        ).json()
        assert done["readiness"]["stage"] == "build_completed"
        assert done["readiness"]["blockers"][0]["id"] == "check:fit"

        # Completion is not acceptance.
        early = client.post(
            f"/v1/create/{pid}/builds/{bid}/accept", json={"decision": "accept"}, headers=PERSON
        )
        assert early.status_code == 409
        assert "Fits the desk edge" in early.json()["detail"]

        ev = client.post(
            f"/v1/create/{pid}/builds/{bid}/evidence",
            json={"kind": "photo", "check_id": "fit", "work_product_id": "wp-photo"},
            headers=AGENT,
        ).json()
        assert ev["creation"]["builds"][0]["results"]["fit"]["evidence_ids"]

        # An agent reports; a person passes a person-reviewed check.
        agent_pass = client.put(
            f"/v1/create/{pid}/builds/{bid}/results/fit", json={"status": "passed"}, headers=AGENT
        )
        assert agent_pass.status_code == 403
        finding = client.put(
            f"/v1/create/{pid}/builds/{bid}/results/fit",
            json={"status": "inconclusive", "note": "Edge out of frame", "confidence": 0.4},
            headers=AGENT,
        )
        assert finding.status_code == 200
        client.put(
            f"/v1/create/{pid}/builds/{bid}/results/fit", json={"status": "passed"}, headers=PERSON
        )

        accepted = client.post(
            f"/v1/create/{pid}/builds/{bid}/accept", json={"decision": "accept"}, headers=PERSON
        ).json()
        assert accepted["readiness"]["stage"] == "accepted_product"
        decision = accepted["creation"]["builds"][0]["acceptance"]
        # The unreviewed, non-blocking finish check stays on the record.
        assert decision["limitations"] == ["Surface finish: awaiting review"]
        assert [h["change"] for h in accepted["creation"]["history"]][-1] == "build_decision"

    def test_release_needs_an_inspection_plan_and_confirmed_critical_dimensions(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend)
        view = _ready(client, pid)
        bid = view["creation"]["builds"][0]["id"]
        client.put(f"/v1/create/{pid}/inspection-plan", json={"checks": []})
        r = client.post(f"/v1/create/{pid}/builds/{bid}/release", json={}, headers=PERSON)
        assert r.status_code == 409 and "inspection plan" in r.json()["detail"]
        client.put(f"/v1/create/{pid}/inspection-plan", json={"checks": _plan()})
        brief = {
            "measurements": [{"id": "t", "label": "Desk thickness", "value": 25, "critical": True}]
        }
        client.put(f"/v1/create/{pid}/brief", json={"brief": brief})
        r = client.post(f"/v1/create/{pid}/builds/{bid}/release", json={}, headers=PERSON)
        assert r.status_code == 409 and "Desk thickness" in r.json()["detail"]

    def test_changing_route_cancels_prepared_builds(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend)
        _ready(client, pid)
        view = client.put(f"/v1/create/{pid}/route", json={"route": {"route": "external"}}).json()
        assert view["creation"]["builds"][0]["status"] == "cancelled"

    def test_delete_refused_while_a_build_is_active(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend)
        bid = _ready(client, pid)["creation"]["builds"][0]["id"]
        client.post(f"/v1/create/{pid}/builds/{bid}/release", json={}, headers=PERSON)
        r = client.delete(f"/v1/create/{pid}")
        assert r.status_code == 409 and "Nothing was deleted" in r.json()["detail"]
        client.post(f"/v1/create/{pid}/builds/{bid}/status", json={"status": "cancelled"})
        assert client.delete(f"/v1/create/{pid}").status_code == 204

    def test_duplicate_copies_the_brief_not_the_builds(
        self, client: TestClient, backend: InMemoryProjectBackend
    ) -> None:
        pid = _project(backend)
        _ready(client, pid)
        r = client.post(f"/v1/create/{pid}/duplicate", json={"name": "Desk hook v2"})
        assert r.status_code == 201
        copy = client.get(f"/v1/create/{r.json()['project_id']}").json()["creation"]
        assert copy["route"]["route"] == "home" and len(copy["inspection_plan"]) == 2
        assert copy["builds"] == []
