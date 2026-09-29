"""Tests for /v1/requirements/quality and /v1/requirements/{id}/fix (FORGE-257)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from api_gateway.requirement_intelligence.models import AgentResult
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity


class TestRequirementQualityRoutes:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.requirement_intelligence.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.requirement_intelligence.routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def _seed(self, twin, project_id, *, name="r1", text="Shall weigh at most 2 kg."):
        c = Constraint(
            name=name,
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="user",
            project_id=project_id,
            message=text,
        )
        return await twin.create_constraint(c)

    async def test_quality_report_for_a_project(self, client, twin) -> None:
        project_id = uuid4()
        await self._seed(twin, project_id)
        async with client:
            resp = await client.get(
                "/v1/requirements/quality",
                params={"project_id": str(project_id), "product_type": "generic"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["requirements"]) == 1
        assert body["completeness"]["productType"] == "generic"

    async def test_invalid_project_id_400s(self, client, twin) -> None:
        async with client:
            resp = await client.get("/v1/requirements/quality", params={"project_id": "not-a-uuid"})
        assert resp.status_code == 400

    async def test_defaults_to_generic_product_type(self, client, twin) -> None:
        project_id = uuid4()
        async with client:
            resp = await client.get(
                "/v1/requirements/quality", params={"project_id": str(project_id)}
            )
        assert resp.status_code == 200
        assert resp.json()["completeness"]["productType"] == "generic"

    async def test_fix_unknown_requirement_404s(self, client, twin) -> None:
        async with client:
            resp = await client.post(f"/v1/requirements/{uuid4()}/fix")
        assert resp.status_code == 404

    async def test_fix_invalid_id_400s(self, client, twin) -> None:
        async with client:
            resp = await client.post("/v1/requirements/not-a-uuid/fix")
        assert resp.status_code == 400

    async def test_fix_proposes_a_rewrite(self, client, twin) -> None:
        project_id = uuid4()
        req = await self._seed(twin, project_id, text="The system should be fast.")

        fake_result = AgentResult(
            conclusions=["The system shall complete a cycle in at most 2 s."],
            assumptions=[],
            evidence=[],
            proposed_patch=None,
            unresolved=[],
            confidence=0.9,
        )
        with patch(
            "api_gateway.requirement_intelligence.routes.RequirementAuthorAgent"
        ) as MockAgent:
            MockAgent.return_value.author = AsyncMock(return_value=fake_result)
            async with client:
                resp = await client.post(f"/v1/requirements/{req.id}/fix")

        assert resp.status_code == 200
        body = resp.json()
        assert body["proposedText"] == "The system shall complete a cycle in at most 2 s."
        assert "weak_modal" in body["rationale"]

        # The agent was called with this requirement as parent_ref, refines relation.
        _, kwargs = MockAgent.return_value.author.call_args
        assert kwargs["parent_ref"] == str(req.id)


class TestRequirementMatrixRoute:
    """GET /v1/requirements/matrix (FORGE-318)."""

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.requirement_intelligence.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.requirement_intelligence.routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def test_matrix_no_data_row_for_unclaimed_requirement(self, client, twin) -> None:
        project_id = uuid4()
        await twin.create_constraint(
            Constraint(
                name="moving_mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mechanical",
                source="user",
                project_id=project_id,
                message="<= 4.5 kg",
            )
        )
        async with client:
            resp = await client.get(
                "/v1/requirements/matrix", params={"project_id": str(project_id)}
            )
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) == 1
        assert rows[0]["status"] == "no_data"
        assert rows[0]["limitText"] == "<= 4.5 kg"

    async def test_invalid_project_id_400s(self, client, twin) -> None:
        async with client:
            resp = await client.get("/v1/requirements/matrix", params={"project_id": "not-a-uuid"})
        assert resp.status_code == 400


class TestCreateConstraintRoute:
    """POST /v1/requirements/constraints (FORGE-259)."""

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.requirement_intelligence.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.requirement_intelligence.routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def test_creates_a_structured_constraint(self, client, twin) -> None:
        project_id = uuid4()
        async with client:
            resp = await client.post(
                "/v1/requirements/constraints",
                json={
                    "projectId": str(project_id),
                    "name": "tip_deflection",
                    "metric": "tip_deflection",
                    "operator": "<=",
                    "limit": 0.5,
                    "unit": "mm",
                    "targetNodeType": "cad_model",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["constraintId"]
            assert body["setWorkProductId"]

            constraint = await twin.get_constraint(UUID(body["constraintId"]))
            assert constraint is not None
            assert constraint.metric == "tip_deflection"
            assert constraint.limit == 0.5
            assert constraint.unit == "mm"

            # It shows up immediately on the live matrix, no_data (no claim yet).
            matrix_resp = await client.get(
                "/v1/requirements/matrix", params={"project_id": str(project_id)}
            )
        rows = matrix_resp.json()["rows"]
        assert len(rows) == 1
        assert rows[0]["status"] == "no_data"
        assert rows[0]["limitText"] == "tip_deflection <= 0.5mm"

    async def test_bad_unit_400s(self, client, twin) -> None:
        async with client:
            resp = await client.post(
                "/v1/requirements/constraints",
                json={
                    "projectId": str(uuid4()),
                    "name": "x",
                    "metric": "mass",
                    "limit": 1.0,
                    "unit": "bogus_unit",
                },
            )
        assert resp.status_code == 400

    async def test_missing_required_field_422s(self, client, twin) -> None:
        async with client:
            resp = await client.post(
                "/v1/requirements/constraints",
                json={"projectId": str(uuid4()), "name": "x"},
            )
        assert resp.status_code == 422
