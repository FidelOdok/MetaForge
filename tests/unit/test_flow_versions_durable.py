"""Flow versions survive a gateway restart (FORGE-482)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.design_flow.spec import FLOWS
from orchestrator.design_flow.versions import (
    FlowVersionStore,
    init_version_store,
    reset_version_store,
)


@pytest.fixture(autouse=True)
def _restore_store():
    yield
    reset_version_store()


def _save(store: FlowVersionStore):
    flow = next(iter(FLOWS.values()))
    return store.save(flow, base_template_id=flow.id, base_version="1.0.0", changes=["x"])


class TestStoreRestart:
    def test_approved_version_survives(self, tmp_path: Path) -> None:
        db = str(tmp_path / "v.db")
        store = FlowVersionStore(db)
        v = _save(store)
        store.attach_approval(v.id, "run_abc")
        store.decide(v.id, approved=True, decided_by="alice")

        again = FlowVersionStore(db).get(v.id)
        assert again.status.value == "approved"
        assert again.decided_by == "alice"
        assert again.approval_id == "run_abc"
        assert again.startable
        assert again.frozen.content_hash == v.frozen.content_hash
        assert again.frozen.version == v.frozen.version
        assert again.definition == v.definition
        again.frozen.verify()

    def test_per_phase_model_override_survives(self, tmp_path: Path) -> None:
        from dataclasses import replace

        flow = next(iter(FLOWS.values()))
        phases = (replace(flow.phases[0], model="anthropic:claude-sonnet-5-5"), *flow.phases[1:])
        db = str(tmp_path / "v.db")
        v = FlowVersionStore(db).save(
            replace(flow, phases=phases), base_template_id=flow.id, base_version="1.0.0", changes=[]
        )
        again = FlowVersionStore(db).get(v.id)
        assert again.definition.phases[0].model == "anthropic:claude-sonnet-5-5"
        assert again.frozen.content_hash == v.frozen.content_hash

    def test_rejected_and_proposed_survive(self, tmp_path: Path) -> None:
        db = str(tmp_path / "v.db")
        store = FlowVersionStore(db)
        rejected, pending = _save(store), _save(store)
        store.decide(rejected.id, approved=False, decided_by="bob")

        reopened = FlowVersionStore(db)
        assert reopened.get(rejected.id).status.value == "rejected"
        assert not reopened.get(rejected.id).startable
        assert reopened.get(pending.id).status.value == "proposed"

    def test_decided_versions_are_immutable_after_restart(self, tmp_path: Path) -> None:
        db = str(tmp_path / "v.db")
        store = FlowVersionStore(db)
        v = _save(store)
        store.decide(v.id, approved=True, decided_by="alice")
        reopened = FlowVersionStore(db)
        with pytest.raises(ValueError, match="already approved"):
            reopened.decide(v.id, approved=False, decided_by="mallory")
        with pytest.raises(ValueError, match="immutable"):
            reopened.attach_approval(v.id, "run_other")

    def test_tampered_row_is_not_restored(self, tmp_path: Path) -> None:
        import sqlite3

        db = str(tmp_path / "v.db")
        v = _save(FlowVersionStore(db))
        conn = sqlite3.connect(db)
        conn.execute("UPDATE flow_versions SET content_hash = 'bad'")
        conn.commit()
        conn.close()
        assert FlowVersionStore(db).list() == []
        assert v.id


class TestApiRestart:
    def test_get_and_run_after_restart(self, tmp_path: Path) -> None:
        from api_gateway.design_flows.routes import router  # noqa: F401
        from api_gateway.server import create_app

        db = str(tmp_path / "v.db")
        init_version_store(db)
        client = TestClient(create_app())
        from tests.unit.test_design_flows_route import _edit_body

        edit = _edit_body(drop="firmware")
        created = client.post("/v1/design-flows/versions", json=edit)
        assert created.status_code == 201, created.text
        version_id = created.json()["versionId"]

        from orchestrator.design_flow.versions import get_version_store

        get_version_store().decide(version_id, approved=True, decided_by="alice")

        init_version_store(db)  # the restart
        client = TestClient(create_app())
        got = client.get(f"/v1/design-flows/versions/{version_id}")
        assert got.status_code == 200, got.text
        assert got.json()["status"] == "approved"

        run = client.post(
            "/v1/runs",
            json={
                "request": {"kind": "design_flow", "flow_version_id": version_id, "goal": "g"},
                "start": False,
            },
        )
        assert run.status_code == 201, run.text
