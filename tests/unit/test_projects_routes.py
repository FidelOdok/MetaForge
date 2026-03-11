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
    def test_returns_seeded_projects(self, client: TestClient) -> None:
        resp = client.get("/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        names = [p["name"] for p in body["projects"]]
        assert "Drone Flight Controller" in names
        assert "IoT Sensor Hub" in names
        assert "Power Supply Module" in names

    def test_project_has_artifacts(self, client: TestClient) -> None:
        resp = client.get("/v1/projects")
        body = resp.json()
        drone = next(p for p in body["projects"] if p["id"] == "proj-001")
        assert len(drone["artifacts"]) == 3
        assert drone["artifacts"][0]["name"] == "Main Schematic"


class TestGetProject:
    def test_get_by_id(self, client: TestClient) -> None:
        resp = client.get("/v1/projects/proj-002")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "IoT Sensor Hub"
        assert body["status"] == "active"

    def test_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.get("/v1/projects/nonexistent")
        assert resp.status_code == 404
