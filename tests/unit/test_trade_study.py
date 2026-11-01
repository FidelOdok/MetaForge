"""Concept generation + trade study (FORGE-262, gap G-B2)."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from api_gateway.twin.trade_study import (
    make_trade_study_selector,
    score_concept_options,
    weighted_score,
)
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType


def test_weighted_score_sums_weight_times_criterion() -> None:
    score = weighted_score({"mass_kg": 1.8, "cost_usd": 340}, {"mass_kg": -1.0, "cost_usd": -0.01})
    assert score == pytest.approx(-1.8 - 3.4)


def test_weighted_score_ignores_criteria_not_in_weights() -> None:
    score = weighted_score({"mass_kg": 1.8, "risk": 5}, {"mass_kg": -1.0})
    assert score == pytest.approx(-1.8)


def test_weighted_score_treats_missing_criterion_as_zero() -> None:
    score = weighted_score({"mass_kg": 1.8}, {"mass_kg": -1.0, "cost_usd": -0.01})
    assert score == pytest.approx(-1.8)


def test_score_concept_options_sorts_highest_first() -> None:
    options = [
        {"id": "a", "criteria_scores": {"mass_kg": 3.0}},
        {"id": "b", "criteria_scores": {"mass_kg": 1.0}},
        {"id": "c", "criteria_scores": {"mass_kg": 2.0}},
    ]
    scored = score_concept_options(options, {"mass_kg": -1.0})
    assert [o["id"] for o in scored] == ["b", "c", "a"]
    assert scored[0]["weighted_score"] == pytest.approx(-1.0)


class TestMakeTradeStudySelector:
    async def _seed_option(
        self,
        twin: InMemoryTwinAPI,
        title: str,
        criteria_scores: dict[str, float],
        evidence_backed_criteria: list[str] | None = None,
    ) -> str:
        record = make_engineering_entity_recorder(twin)
        result = await record(
            entity_type="concept_option",
            statement=f"candidate architecture: {title}",
            title=title,
            extra={
                "criteria_scores": criteria_scores,
                "evidence_backed_criteria": evidence_backed_criteria or [],
            },
        )
        return result["node_id"]

    async def test_selects_and_records_a_real_decision_with_alternatives(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)

        solid = await self._seed_option(twin, "Solid bar", {"mass_kg": 3.2, "cost_usd": 40})
        hollow = await self._seed_option(
            twin, "Hollow tube", {"mass_kg": 1.6, "cost_usd": 55}, ["mass_kg"]
        )

        result = await select(
            option_ids=[solid, hollow],
            selected_option_id=hollow,
            weights={"mass_kg": -1.0, "cost_usd": -0.01},
            title="Upper arm link architecture",
            rationale="Hollow tube minimises mass at an acceptable cost premium.",
        )

        assert result["selected_option_id"] == hollow
        decision_wp = await twin.get_work_product(UUID(result["node_id"]))
        assert decision_wp is not None
        assert decision_wp.type == WorkProductType.DESIGN_DECISION
        alternatives = decision_wp.metadata["alternatives"]
        assert len(alternatives) == 1
        assert alternatives[0]["option"] == "Solid bar"
        assert "weighted score" in alternatives[0]["reason_rejected"]

        # G5's own "Trade study performed" check reads exactly this --
        # non-empty alternatives on a real recorded Decision.
        assert decision_wp.metadata["alternatives"]

    async def test_links_decision_to_selected_option_via_generated_from(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)

        a = await self._seed_option(twin, "A", {"mass_kg": 2.0})
        b = await self._seed_option(twin, "B", {"mass_kg": 1.0})

        result = await select(
            option_ids=[a, b],
            selected_option_id=b,
            weights={"mass_kg": -1.0},
            title="T",
            rationale="R",
        )
        edges = await twin.get_edges(UUID(result["node_id"]), edge_type=EdgeType.GENERATED_FROM)
        assert len(edges) == 1
        assert str(edges[0].target_id) == b

    async def test_selected_option_need_not_have_the_highest_weighted_score(self) -> None:
        """A human/agent can override the arithmetic winner -- the tool
        computes and reports scores, it doesn't enforce the "best" pick."""
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)

        light = await self._seed_option(twin, "Light", {"mass_kg": 1.0, "risk": 8})
        safer = await self._seed_option(twin, "Safer", {"mass_kg": 2.0, "risk": 1})

        result = await select(
            option_ids=[light, safer],
            selected_option_id=safer,
            weights={"mass_kg": -1.0, "risk": -1.0},
            title="T",
            rationale="Risk outweighs the mass penalty for this application.",
        )
        assert result["selected_option_id"] == safer

    async def test_selected_option_id_must_be_one_of_option_ids(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        a = await self._seed_option(twin, "A", {"mass_kg": 1.0})

        with pytest.raises(ValueError, match="selected_option_id"):
            await select(
                option_ids=[a],
                selected_option_id="11111111-1111-1111-1111-111111111111",
                weights={"mass_kg": -1.0},
                title="T",
                rationale="R",
            )

    async def test_unknown_option_id_raises(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)

        with pytest.raises(ValueError, match="no concept_option"):
            await select(
                option_ids=["11111111-1111-1111-1111-111111111111"],
                selected_option_id="11111111-1111-1111-1111-111111111111",
                weights={"mass_kg": -1.0},
                title="T",
                rationale="R",
            )

    async def test_non_concept_option_entity_id_raises(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_engineering_entity_recorder(twin)
        risk = await record(entity_type="risk", statement="battery thermal runaway")
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)

        with pytest.raises(ValueError, match="not a concept_option"):
            await select(
                option_ids=[risk["node_id"]],
                selected_option_id=risk["node_id"],
                weights={"mass_kg": -1.0},
                title="T",
                rationale="R",
            )

    async def test_links_decision_to_requirement_ids(self) -> None:
        from twin_core.models.constraint import Constraint
        from twin_core.models.enums import ConstraintSeverity

        twin = InMemoryTwinAPI.create()
        req = await twin.create_constraint(
            Constraint(
                name="mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
            )
        )
        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        a = await self._seed_option(twin, "A", {"mass_kg": 1.0})

        result = await select(
            option_ids=[a],
            selected_option_id=a,
            weights={"mass_kg": -1.0},
            title="T",
            rationale="R",
            requirement_ids=[str(req.id)],
        )
        decision_wp = await twin.get_work_product(UUID(result["node_id"]))
        assert decision_wp is not None
        assert decision_wp.metadata["parent_refs"] == [str(req.id)]


class TestG5GateIntegration:
    """Integration proof (not just a unit test of the gate check in
    isolation, which tests/unit/test_g4_g5_gates.py already covers): a real
    twin.select_concept call is exactly what flips
    evaluate_g5_concept_selection's "Trade study performed" check from
    NOT_EVALUATED to PASS -- the actual gap this ticket closes."""

    async def test_select_concept_flips_trade_study_check_to_pass(self) -> None:
        from uuid import uuid4

        from twin_core.consistency import GateCheckStatus, evaluate_g5_concept_selection

        twin = InMemoryTwinAPI.create()
        project_id = uuid4()
        record = make_engineering_entity_recorder(twin)
        a = await record(
            entity_type="concept_option",
            statement="Solid bar",
            title="Solid bar",
            extra={"criteria_scores": {"mass_kg": 3.2}},
            project_id=str(project_id),
        )
        b = await record(
            entity_type="concept_option",
            statement="Hollow tube",
            title="Hollow tube",
            extra={"criteria_scores": {"mass_kg": 1.6}},
            project_id=str(project_id),
        )

        before = await evaluate_g5_concept_selection(twin, project_id)
        before_check = next(c for c in before.checks if c.id == "decisions:none-recorded")
        assert before_check.status == GateCheckStatus.NOT_EVALUATED

        decision_recorder = make_decision_recorder(twin)
        select = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        result = await select(
            option_ids=[a["node_id"], b["node_id"]],
            selected_option_id=b["node_id"],
            weights={"mass_kg": -1.0},
            title="Upper arm link architecture",
            rationale="Hollow tube minimises mass.",
            project_id=str(project_id),
        )

        after = await evaluate_g5_concept_selection(twin, project_id)
        trade_study_check = next(
            c for c in after.checks if c.id == f"decision:{result['node_id']}:trade_study"
        )
        assert trade_study_check.status == GateCheckStatus.PASS
        rationale_check = next(
            c for c in after.checks if c.id == f"decision:{result['node_id']}:rationale"
        )
        assert rationale_check.status == GateCheckStatus.PASS


class TestSelectConceptAdapter:
    async def test_tool_registered_and_returns_shape(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        selector = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        server = TwinServer(twin=twin, concept_selector=selector)
        assert "twin.select_concept" in server.tool_ids

        record = make_engineering_entity_recorder(twin)
        a = await record(
            entity_type="concept_option",
            statement="A",
            title="A",
            extra={"criteria_scores": {"mass_kg": 1.0}},
        )
        b = await record(
            entity_type="concept_option",
            statement="B",
            title="B",
            extra={"criteria_scores": {"mass_kg": 2.0}},
        )
        out = await server.select_concept(
            {
                "option_ids": [a["node_id"], b["node_id"]],
                "selected_option_id": a["node_id"],
                "weights": {"mass_kg": -1.0},
                "title": "T",
                "rationale": "R",
            }
        )
        assert out["selected_option_id"] == a["node_id"]

    def test_not_registered_when_none_supplied(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.select_concept" not in server.tool_ids

    async def test_missing_option_ids_rejected(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        selector = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        server = TwinServer(twin=twin, concept_selector=selector)
        with pytest.raises(ValueError, match="option_ids"):
            await server.select_concept(
                {
                    "selected_option_id": "x",
                    "weights": {"mass_kg": -1.0},
                    "title": "T",
                    "rationale": "R",
                }
            )

    async def test_missing_weights_rejected(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        selector = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        server = TwinServer(twin=twin, concept_selector=selector)
        with pytest.raises(ValueError, match="weights"):
            await server.select_concept(
                {
                    "option_ids": ["x"],
                    "selected_option_id": "x",
                    "title": "T",
                    "rationale": "R",
                }
            )


class TestTradeStudyRoutes:
    @pytest.fixture
    def twin(self) -> InMemoryTwinAPI:
        return InMemoryTwinAPI.create()

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.trade_study.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture(autouse=True)
    def _wire(self, twin: InMemoryTwinAPI):
        from api_gateway.trade_study.routes import init_concept_selector, init_entity_recorder
        from api_gateway.trade_study.routes import init_twin as init_trade_study_twin

        decision_recorder = make_decision_recorder(twin)
        selector = make_trade_study_selector(twin, decision_recorder=decision_recorder)
        init_trade_study_twin(twin)
        init_concept_selector(selector)
        init_entity_recorder(make_engineering_entity_recorder(twin))
        yield
        init_concept_selector(None)
        init_entity_recorder(None)
        init_trade_study_twin(InMemoryTwinAPI.create())

    async def test_add_option_then_it_appears_in_the_list(self, client) -> None:
        async with client:
            add_resp = await client.post(
                "/v1/trade-study/options",
                json={
                    "title": "Hollow tube",
                    "criteriaScores": {"mass_kg": 1.6, "cost_usd": 55},
                    "evidenceBackedCriteria": ["mass_kg"],
                },
            )
            assert add_resp.status_code == 200
            node_id = add_resp.json()["node_id"]

            list_resp = await client.get("/v1/trade-study/options")
            options = list_resp.json()["options"]
        assert [o["id"] for o in options] == [node_id]
        assert options[0]["criteria_scores"] == {"mass_kg": 1.6, "cost_usd": 55}
        assert options[0]["evidence_backed_criteria"] == ["mass_kg"]

    async def test_entity_recorder_unavailable_503s(self, client) -> None:
        from api_gateway.trade_study.routes import init_entity_recorder

        init_entity_recorder(None)
        async with client:
            resp = await client.post(
                "/v1/trade-study/options",
                json={"title": "T", "criteriaScores": {"mass_kg": 1.0}},
            )
        assert resp.status_code == 503

    async def test_list_options_then_select_round_trip(self, client, twin: InMemoryTwinAPI) -> None:
        record = make_engineering_entity_recorder(twin)
        a = await record(
            entity_type="concept_option",
            statement="Solid bar",
            title="Solid bar",
            extra={"criteria_scores": {"mass_kg": 3.2}},
        )
        b = await record(
            entity_type="concept_option",
            statement="Hollow tube",
            title="Hollow tube",
            extra={"criteria_scores": {"mass_kg": 1.6}, "evidence_backed_criteria": ["mass_kg"]},
        )

        async with client:
            list_resp = await client.get("/v1/trade-study/options")
            assert list_resp.status_code == 200
            options = list_resp.json()["options"]
            assert {o["id"] for o in options} == {a["node_id"], b["node_id"]}

            select_resp = await client.post(
                "/v1/trade-study/select",
                json={
                    "optionIds": [a["node_id"], b["node_id"]],
                    "selectedOptionId": b["node_id"],
                    "weights": {"mass_kg": -1.0},
                    "title": "Upper arm link architecture",
                    "rationale": "Hollow tube minimises mass.",
                },
            )
            assert select_resp.status_code == 200
            assert select_resp.json()["selected_option_id"] == b["node_id"]

    async def test_selected_option_not_in_option_ids_400s(
        self, client, twin: InMemoryTwinAPI
    ) -> None:
        record = make_engineering_entity_recorder(twin)
        a = await record(
            entity_type="concept_option",
            statement="A",
            title="A",
            extra={"criteria_scores": {"mass_kg": 1.0}},
        )
        async with client:
            resp = await client.post(
                "/v1/trade-study/select",
                json={
                    "optionIds": [a["node_id"]],
                    "selectedOptionId": "11111111-1111-1111-1111-111111111111",
                    "weights": {"mass_kg": -1.0},
                    "title": "T",
                    "rationale": "R",
                },
            )
        assert resp.status_code == 400

    async def test_selector_unavailable_503s(self, client) -> None:
        from api_gateway.trade_study.routes import init_concept_selector

        init_concept_selector(None)
        async with client:
            resp = await client.post(
                "/v1/trade-study/select",
                json={
                    "optionIds": ["x"],
                    "selectedOptionId": "x",
                    "weights": {"mass_kg": -1.0},
                    "title": "T",
                    "rationale": "R",
                },
            )
        assert resp.status_code == 503

    async def test_empty_project_returns_empty_options(self, client) -> None:
        async with client:
            resp = await client.get(
                "/v1/trade-study/options",
                params={"project_id": "11111111-1111-1111-1111-111111111111"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"options": []}
