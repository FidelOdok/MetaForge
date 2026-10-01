"""Tests for the BOM risk gateway route (FORGE-268).

Same lightweight-FastAPI-app + fake-callable pattern
test_manufacture_routes.py established for
api_gateway/manufacture/routes.py's injected-closure seam
(init_manufacture_release), adapted for api_gateway/bom/risk_routes.py's
init_bom_risk_scorer.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.bom.risk_routes import init_bom_risk_scorer, router


class _FakeScorer:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def __call__(self, project_id: str) -> dict[str, Any]:
        self.calls.append(project_id)
        if self.fail is not None:
            raise self.fail
        return {
            "project_id": project_id,
            "total_parts": 1,
            "overall_score": 10,
            "critical_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 1,
            "part_scores": [],
        }


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestBomRiskRoute:
    def test_success(self, client: TestClient) -> None:
        scorer = _FakeScorer()
        init_bom_risk_scorer(scorer)

        response = client.get("/v1/bom/risk", params={"project_id": "proj-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["overall_score"] == 10
        assert scorer.calls == ["proj-1"]

    def test_value_error_returns_400(self, client: TestClient) -> None:
        init_bom_risk_scorer(_FakeScorer(fail=ValueError("bad project id")))

        response = client.get("/v1/bom/risk", params={"project_id": "proj-1"})

        assert response.status_code == 400
        assert "bad project id" in response.json()["detail"]

    def test_unexpected_failure_returns_502(self, client: TestClient) -> None:
        init_bom_risk_scorer(_FakeScorer(fail=RuntimeError("boom")))

        response = client.get("/v1/bom/risk", params={"project_id": "proj-1"})

        assert response.status_code == 502

    def test_not_configured_returns_503(self, client: TestClient) -> None:
        import api_gateway.bom.risk_routes as routes_module

        routes_module._score_risk = None  # noqa: SLF001 -- reset the module-level seam

        response = client.get("/v1/bom/risk", params={"project_id": "proj-1"})

        assert response.status_code == 503

    def test_missing_query_param_returns_422(self, client: TestClient) -> None:
        init_bom_risk_scorer(_FakeScorer())
        response = client.get("/v1/bom/risk")
        assert response.status_code == 422
