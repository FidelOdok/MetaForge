"""Integration test for ``GET /v1/memory/insights`` (MET-454/455)."""

from __future__ import annotations

from uuid import uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.memory import router as memory_router
from digital_twin.memory.consolidation.insight import (
    Insight,
    InsightKind,
    InsightStatus,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InMemoryInsightStore


def _insight(
    *,
    theme: ConsolidationTheme = ConsolidationTheme.MECHANICAL_VALIDATION,
    status: InsightStatus = InsightStatus.ACTIVE,
    narrative: str = "A long enough narrative to satisfy the model constraints",
) -> Insight:
    return Insight(
        id=uuid4(),
        theme=theme,
        kind=InsightKind.PRINCIPLE,
        narrative=narrative,
        confidence=0.85,
        supporting_experience_ids=[uuid4()],
        status=status,
    )


@pytest.fixture
def app_with_insights() -> tuple[FastAPI, InMemoryInsightStore]:
    store = InMemoryInsightStore()
    app = FastAPI()
    app.state.consolidation_insight_store = store
    app.include_router(memory_router)
    return app, store


def test_list_insights_excludes_stale_by_default(app_with_insights):
    app, store = app_with_insights
    anyio.run(store.write, _insight(status=InsightStatus.ACTIVE, narrative="active one here ok"))
    anyio.run(store.write, _insight(status=InsightStatus.STALE_WARN, narrative="stale one here ok"))

    with TestClient(app) as client:
        response = client.get("/v1/memory/insights")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["insights"][0]["status"] == "active"
    assert body["includeStale"] is False


def test_list_insights_include_stale(app_with_insights):
    app, store = app_with_insights
    anyio.run(store.write, _insight(status=InsightStatus.ACTIVE, narrative="active one here ok"))
    anyio.run(store.write, _insight(status=InsightStatus.STALE_WARN, narrative="stale one here ok"))

    with TestClient(app) as client:
        response = client.get("/v1/memory/insights", params={"includeStale": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["includeStale"] is True


def test_list_insights_theme_filter(app_with_insights):
    app, store = app_with_insights
    anyio.run(store.write, _insight(theme=ConsolidationTheme.MECHANICAL_VALIDATION))
    anyio.run(store.write, _insight(theme=ConsolidationTheme.POWER_ANALYSIS))

    with TestClient(app) as client:
        response = client.get(
            "/v1/memory/insights", params={"theme": "power_analysis"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["insights"][0]["theme"] == "power_analysis"


def test_list_insights_503_when_store_unbound():
    app = FastAPI()
    app.include_router(memory_router)
    with TestClient(app) as client:
        response = client.get("/v1/memory/insights")
    assert response.status_code == 503
    assert response.json()["detail"] == "consolidation_insight_store_not_ready"


def test_list_insights_empty_store_returns_empty(app_with_insights):
    app, _store = app_with_insights
    with TestClient(app) as client:
        response = client.get("/v1/memory/insights")
    assert response.status_code == 200
    assert response.json() == {
        "insights": [],
        "total": 0,
        "theme": None,
        "includeStale": False,
    }


def test_list_insights_respects_limit(app_with_insights):
    app, store = app_with_insights
    for i in range(5):
        anyio.run(
            store.write,
            _insight(narrative=f"active insight number {i} long enough"),
        )
    with TestClient(app) as client:
        response = client.get("/v1/memory/insights", params={"limit": 2})
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_list_insights_invalid_limit_422(app_with_insights):
    app, _store = app_with_insights
    with TestClient(app) as client:
        response = client.get("/v1/memory/insights", params={"limit": 9999})
    assert response.status_code == 422
