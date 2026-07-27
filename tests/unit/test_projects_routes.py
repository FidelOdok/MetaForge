"""Unit tests for the projects REST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_gateway.server import create_app
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import InMemoryWorkflowEngine


@pytest.fixture
def client() -> TestClient:
    engine = InMemoryWorkflowEngine.create()
    app = create_app(workflow_engine=engine, scheduler=InMemoryScheduler.__new__(InMemoryScheduler))
    return TestClient(app)


class TestListProjects:
    def test_returns_empty_initially(self, client: TestClient) -> None:
        resp = client.get("/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["projects"] == []


class TestCreateProject:
    def test_create_and_list(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/projects",
            json={"name": "Drone Flight Controller", "description": "A test project"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Drone Flight Controller"
        assert body["description"] == "A test project"
        assert body["status"] == "draft"

        # Verify it appears in the list
        list_resp = client.get("/v1/projects")
        assert list_resp.json()["total"] == 1

    def test_duplicate_name_returns_409(self, client: TestClient) -> None:
        client.post("/v1/projects", json={"name": "Quadruped Robot"})
        resp = client.post("/v1/projects", json={"name": "quadruped robot"})
        assert resp.status_code == 409


class TestGetProject:
    def test_get_created_project(self, client: TestClient) -> None:
        create_resp = client.post(
            "/v1/projects",
            json={"name": "IoT Sensor Hub"},
        )
        project_id = create_resp.json()["id"]

        resp = client.get(f"/v1/projects/{project_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "IoT Sensor Hub"

    def test_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.get("/v1/projects/nonexistent")
        assert resp.status_code == 404


class TestUpdateProject:
    def test_rename(self, client: TestClient) -> None:
        project_id = client.post("/v1/projects", json={"name": "old"}).json()["id"]
        resp = client.patch(f"/v1/projects/{project_id}", json={"name": "new"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "new"

    def test_status_change_leaves_name_untouched(self, client: TestClient) -> None:
        project_id = client.post("/v1/projects", json={"name": "keep-me"}).json()["id"]
        resp = client.patch(f"/v1/projects/{project_id}", json={"status": "active"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["name"] == "keep-me"

    def test_empty_body_returns_400(self, client: TestClient) -> None:
        project_id = client.post("/v1/projects", json={"name": "p"}).json()["id"]
        resp = client.patch(f"/v1/projects/{project_id}", json={})
        assert resp.status_code == 400

    def test_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.patch("/v1/projects/nonexistent", json={"name": "x"})
        assert resp.status_code == 404

    def test_rename_conflict_returns_409(self, client: TestClient) -> None:
        client.post("/v1/projects", json={"name": "taken"})
        other_id = client.post("/v1/projects", json={"name": "renamable"}).json()["id"]
        resp = client.patch(f"/v1/projects/{other_id}", json={"name": "TAKEN"})
        assert resp.status_code == 409


class TestDeleteProject:
    def test_delete_project(self, client: TestClient) -> None:
        create_resp = client.post(
            "/v1/projects",
            json={"name": "To Delete"},
        )
        project_id = create_resp.json()["id"]

        resp = client.delete(f"/v1/projects/{project_id}")
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/v1/projects/{project_id}")
        assert get_resp.status_code == 404

    def test_delete_nonexistent(self, client: TestClient) -> None:
        resp = client.delete("/v1/projects/nonexistent")
        assert resp.status_code == 404


class TestUnlinkWorkProduct:
    """DELETE /v1/projects/{id}/work-products/{wp_id} (MET-484)."""

    async def test_unlink_removes_only_the_target(self, client: TestClient) -> None:
        from api_gateway.projects.routes import link_work_product_to_project

        pid = client.post("/v1/projects", json={"name": "Unlink P"}).json()["id"]
        await link_work_product_to_project(pid, "wp-1", "WP One", "bom")
        await link_work_product_to_project(pid, "wp-2", "WP Two", "prd")
        ids = {w["id"] for w in client.get(f"/v1/projects/{pid}").json()["work_products"]}
        assert {"wp-1", "wp-2"} <= ids

        resp = client.delete(f"/v1/projects/{pid}/work-products/wp-1")
        assert resp.status_code == 204

        ids_after = {w["id"] for w in client.get(f"/v1/projects/{pid}").json()["work_products"]}
        assert "wp-1" not in ids_after
        assert "wp-2" in ids_after

    async def test_unlink_unknown_returns_404(self, client: TestClient) -> None:
        pid = client.post("/v1/projects", json={"name": "Unlink P2"}).json()["id"]
        resp = client.delete(f"/v1/projects/{pid}/work-products/nope")
        assert resp.status_code == 404
