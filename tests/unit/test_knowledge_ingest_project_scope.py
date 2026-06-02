"""Gateway ingest must forward project_id to the service (MET-485).

Regression: `/v1/knowledge/ingest` and `/documents` previously dropped
project scope, so every ingest landed in the `default` tenant and
project-scoped views (KnowledgePage) came back empty.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.knowledge.routes import router

PROJECT = "259d9bb0-1407-42e5-918a-0f1ac5ee33fb"


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def ingest(
        self,
        *,
        content: str,
        source_path: str,
        knowledge_type: object,
        source_work_product_id: object | None = None,
        metadata: object | None = None,
        project_id: UUID | None = None,
    ):
        self.calls.append({"source_path": source_path, "project_id": project_id})
        return SimpleNamespace(entry_ids=[uuid4()], chunks_indexed=1, source_path=source_path)


@pytest.fixture
def client_and_service():
    app = FastAPI()
    app.include_router(router)
    svc = _RecordingService()
    app.state.knowledge_service = svc
    return TestClient(app), svc


def test_ingest_forwards_project_id(client_and_service) -> None:
    client, svc = client_and_service
    resp = client.post(
        "/v1/knowledge/ingest",
        json={
            "content": "ESP32-WROOM-32 datasheet text",
            "knowledgeType": "component",
            "sourcePath": "datasheet://esp32-wroom-32",
            "projectId": PROJECT,
        },
    )
    assert resp.status_code == 201, resp.text
    assert svc.calls[0]["project_id"] == UUID(PROJECT)


def test_ingest_without_project_id_is_default(client_and_service) -> None:
    client, svc = client_and_service
    resp = client.post(
        "/v1/knowledge/ingest",
        json={
            "content": "shared component note",
            "knowledgeType": "component",
            "sourcePath": "datasheet://shared",
        },
    )
    assert resp.status_code == 201, resp.text
    assert svc.calls[0]["project_id"] is None


def test_document_ingest_forwards_project_id(client_and_service) -> None:
    client, svc = client_and_service
    resp = client.post(
        "/v1/knowledge/documents",
        json={
            "content": "# Design decision\nUse a single 3.3V rail.",
            "sourcePath": "decision://drone/rail",
            "knowledgeType": "design_decision",
            "projectId": PROJECT,
        },
    )
    assert resp.status_code == 201, resp.text
    assert svc.calls[0]["project_id"] == UUID(PROJECT)
