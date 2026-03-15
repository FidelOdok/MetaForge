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
