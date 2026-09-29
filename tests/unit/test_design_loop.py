"""Unit tests for the closed design loop (FORGE-287): the orchestration
layer (api_gateway.twin.design_loop), MCP tool registration, and REST
routes."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.twin.design_loop import (
    make_design_loop_approver,
    make_design_loop_reader,
    make_design_loop_starter,
)
from api_gateway.twin.optimizer import make_wall_thickness_optimizer
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _seed_cad(twin: InMemoryTwinAPI, name: str = "upper_arm") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            metadata={
                "geometry_features": {
                    "properties": {
                        "bounding_box": {
                            "min_x": -180,
                            "max_x": 180,
                            "min_y": -20,
                            "max_y": 20,
                            "min_z": -30,
                            "max_z": 30,
                        }
                    }
                }
            },
        )
    )


class TestMakeDesignLoopStarter:
    async def test_optimal_run_persists_every_candidate(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        out = await start(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert out["status"] == "optimal"
        assert out["loop_id"]
        assert out["iteration_count"] == len(out["candidates"])
        assert out["iteration_count"] == len(out["iteration_ids"])

        iterations = await twin.list_design_loop_iterations(UUID(out["loop_id"]))
        assert len(iterations) == out["iteration_count"]
        assert [it.iteration_number for it in iterations] == list(range(len(iterations)))

        winners = [it for it in iterations if it.is_winner]
        assert len(winners) == 1
        assert winners[0].status == "converged"
        assert winners[0].feasible is True
        assert winners[0].parameter_name == "wall_thickness_mm"
        assert winners[0].metric == "mass_kg"

    async def test_sequence_edges_link_iterations(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        out = await start(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        iterations = await twin.list_design_loop_iterations(UUID(out["loop_id"]))
        assert len(iterations) >= 2
        for prev, cur in zip(iterations, iterations[1:], strict=False):
            edges = await twin.get_edges(
                cur.id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
            )
            assert any(e.target_id == prev.id for e in edges)

    async def test_infeasible_run_marks_last_iteration(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        out = await start(
            work_product_id=str(wp.id),
            load_n=20.0,
            deflection_limit_mm=0.0001,
            sf_limit=2.0,
        )
        assert out["status"] == "infeasible"
        assert out["winner"] is None
        iterations = await twin.list_design_loop_iterations(UUID(out["loop_id"]))
        assert not any(it.is_winner for it in iterations)
        assert iterations[-1].status == "infeasible"

    async def test_already_feasible_at_min_marks_first_iteration_winner(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        out = await start(
            work_product_id=str(wp.id),
            load_n=20.0,
            deflection_limit_mm=1000.0,
            sf_limit=0.001,
            wall_min_mm=2.0,
        )
        assert out["status"] == "already_feasible_at_min"
        iterations = await twin.list_design_loop_iterations(UUID(out["loop_id"]))
        assert iterations[0].is_winner is True
        assert iterations[0].status == "converged"

    async def test_links_iterations_to_requirement_ids(self, twin: InMemoryTwinAPI) -> None:
        from twin_core.models.constraint import Constraint
        from twin_core.models.enums import ConstraintSeverity

        wp = await _seed_cad(twin)
        req = await twin.create_constraint(
            Constraint(
                name="tip_deflection",
                expression="True",
                message="<= 0.5mm",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
            )
        )
        start = make_design_loop_starter(twin)
        out = await start(
            work_product_id=str(wp.id),
            load_n=100.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
            requirement_ids=[str(req.id)],
        )
        iterations = await twin.list_design_loop_iterations(UUID(out["loop_id"]))
        for it in iterations:
            edges = await twin.get_edges(
                it.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
            )
            assert any(e.target_id == req.id for e in edges)

    async def test_reuses_supplied_optimizer_for_evidence_decision(
        self, twin: InMemoryTwinAPI
    ) -> None:
        from api_gateway.twin.decision_recorder import make_decision_recorder
        from api_gateway.twin.evidence_recorder import make_evidence_recorder

        wp = await _seed_cad(twin)
        evidence_recorder = make_evidence_recorder(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(
            twin, evidence_recorder=evidence_recorder, decision_recorder=decision_recorder
        )
        start = make_design_loop_starter(twin, optimize=optimize)
        out = await start(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert "evidence_node_id" in out
        assert "decision_node_id" in out


class TestMakeDesignLoopReader:
    async def test_unknown_loop_raises(self, twin: InMemoryTwinAPI) -> None:
        read = make_design_loop_reader(twin)
        with pytest.raises(ValueError, match="no iterations found"):
            await read(loop_id="11111111-1111-1111-1111-111111111111")

    async def test_returns_sorted_iteration_timeline(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        started = await start(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        read = make_design_loop_reader(twin)
        out = await read(loop_id=started["loop_id"])
        assert out["loop_id"] == started["loop_id"]
        numbers = [it["iteration_number"] for it in out["iterations"]]
        assert numbers == sorted(numbers)


class TestMakeDesignLoopApprover:
    async def test_approves_the_winning_iteration(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        started = await start(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        approve = make_design_loop_approver(twin)
        out = await approve(
            loop_id=started["loop_id"], approved_by="fidel.odok@idroneinnovations.com"
        )
        assert out["approved"] is True
        assert out["approved_by"] == "fidel.odok@idroneinnovations.com"
        assert out["approved_at"] is not None

    async def test_raises_when_loop_never_converged(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        start = make_design_loop_starter(twin)
        started = await start(
            work_product_id=str(wp.id),
            load_n=20.0,
            deflection_limit_mm=0.0001,
            sf_limit=2.0,
        )
        assert started["status"] == "infeasible"
        approve = make_design_loop_approver(twin)
        with pytest.raises(ValueError, match="no winning iteration"):
            await approve(loop_id=started["loop_id"], approved_by="someone")


class TestDesignLoopAdapter:
    async def test_tools_registered_and_return_shape(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        starter = make_design_loop_starter(twin)
        reader = make_design_loop_reader(twin)
        approver = make_design_loop_approver(twin)
        server = TwinServer(
            twin=twin,
            design_loop_starter=starter,
            design_loop_reader=reader,
            design_loop_approver=approver,
        )
        assert "twin.start_design_loop" in server.tool_ids
        assert "twin.get_design_loop" in server.tool_ids
        assert "twin.approve_design_loop" in server.tool_ids

        started = await server.start_design_loop(
            {"work_product_id": str(wp.id), "load_n": 100.0, "deflection_limit_mm": 0.5}
        )
        assert started["status"] == "optimal"

        fetched = await server.get_design_loop({"loop_id": started["loop_id"]})
        assert fetched["loop_id"] == started["loop_id"]

        approved = await server.approve_design_loop(
            {"loop_id": started["loop_id"], "approved_by": "reviewer"}
        )
        assert approved["approved"] is True

    async def test_not_registered_when_none_supplied(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.start_design_loop" not in server.tool_ids
        assert "twin.get_design_loop" not in server.tool_ids
        assert "twin.approve_design_loop" not in server.tool_ids

    async def test_missing_work_product_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin, design_loop_starter=make_design_loop_starter(twin))
        with pytest.raises(ValueError, match="work_product_id"):
            await server.start_design_loop({"load_n": 20.0, "deflection_limit_mm": 0.5})

    async def test_missing_loop_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin, design_loop_reader=make_design_loop_reader(twin))
        with pytest.raises(ValueError, match="loop_id"):
            await server.get_design_loop({})

    async def test_missing_approved_by_rejected(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin, design_loop_approver=make_design_loop_approver(twin))
        with pytest.raises(ValueError, match="approved_by"):
            await server.approve_design_loop({"loop_id": "x"})


class TestDesignLoopRoutes:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.design_loop.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture(autouse=True)
    def _wire(self, twin: InMemoryTwinAPI):
        from api_gateway.design_loop.routes import init_design_loop_starter
        from api_gateway.design_loop.routes import init_twin as init_design_loop_twin

        init_design_loop_twin(twin)
        init_design_loop_starter(make_design_loop_starter(twin))
        yield
        init_design_loop_starter(None)
        init_design_loop_twin(InMemoryTwinAPI.create())

    async def test_start_get_approve_round_trip(self, client, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        async with client:
            start_resp = await client.post(
                "/v1/design-loop/start",
                json={
                    "workProductId": str(wp.id),
                    "loadN": 100.0,
                    "deflectionLimitMm": 0.5,
                    "sfLimit": 2.0,
                },
            )
            assert start_resp.status_code == 200
            loop_id = start_resp.json()["loop_id"]

            get_resp = await client.get(f"/v1/design-loop/{loop_id}")
            assert get_resp.status_code == 200
            assert len(get_resp.json()["iterations"]) > 0

            approve_resp = await client.post(
                f"/v1/design-loop/{loop_id}/approve",
                json={"approvedBy": "reviewer@example.com"},
            )
            assert approve_resp.status_code == 200
            assert approve_resp.json()["approved"] is True

    async def test_unknown_loop_404s(self, client) -> None:
        async with client:
            resp = await client.get("/v1/design-loop/11111111-1111-1111-1111-111111111111")
        assert resp.status_code == 404

    async def test_missing_work_product_400s(self, client) -> None:
        async with client:
            resp = await client.post(
                "/v1/design-loop/start",
                json={
                    "workProductId": "11111111-1111-1111-1111-111111111111",
                    "loadN": 20.0,
                    "deflectionLimitMm": 0.5,
                },
            )
        assert resp.status_code == 400

    async def test_starter_unavailable_503s(self, client) -> None:
        from api_gateway.design_loop.routes import init_design_loop_starter

        init_design_loop_starter(None)
        async with client:
            resp = await client.post(
                "/v1/design-loop/start",
                json={
                    "workProductId": "11111111-1111-1111-1111-111111111111",
                    "loadN": 20.0,
                    "deflectionLimitMm": 0.5,
                },
            )
        assert resp.status_code == 503
