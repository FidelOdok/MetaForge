"""Requirement-driven component selection (FORGE-265, gap G-C1)."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.twin.component_recorder import make_component_recorder
from api_gateway.twin.component_selection import (
    check_candidate_against_requirements,
    check_spec_margin,
    make_component_selector,
)
from api_gateway.twin.decision_recorder import make_decision_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType


def test_check_spec_margin_gte_pass() -> None:
    result = check_spec_margin(20.0, 24.5, ">=")
    assert result["pass"] is True
    assert result["margin"] == pytest.approx(4.5)
    assert result["margin_pct"] == pytest.approx(22.5)


def test_check_spec_margin_gte_fail() -> None:
    result = check_spec_margin(20.0, 19.6, ">=")
    assert result["pass"] is False
    assert result["margin"] == pytest.approx(-0.4)


def test_check_spec_margin_lte_pass() -> None:
    result = check_spec_margin(6.0, 5.0, "<=")
    assert result["pass"] is True
    assert result["margin"] == pytest.approx(1.0)


def test_check_spec_margin_lte_fail() -> None:
    result = check_spec_margin(6.0, 7.4, "<=")
    assert result["pass"] is False
    assert result["margin"] == pytest.approx(-1.4)


def test_check_spec_margin_zero_required_gives_none_pct() -> None:
    result = check_spec_margin(0.0, 5.0, ">=")
    assert result["margin_pct"] is None


def test_check_spec_margin_invalid_op_raises() -> None:
    with pytest.raises(ValueError, match="op"):
        check_spec_margin(1.0, 2.0, "==")  # type: ignore[arg-type]


def test_check_candidate_against_requirements_reports_missing_spec() -> None:
    margins = check_candidate_against_requirements(
        {"torque_kg_cm": 25.0}, {"voltage_range": {"op": "<=", "value": 6.0}}
    )
    assert margins["voltage_range"]["pass"] is False
    assert margins["voltage_range"]["margin"] is None
    assert "no recorded value" in margins["voltage_range"]["error"]


def test_check_candidate_against_requirements_scores_present_specs() -> None:
    margins = check_candidate_against_requirements(
        {"torque_kg_cm": 25.0}, {"torque_kg_cm": {"op": ">=", "value": 20.0}}
    )
    assert margins["torque_kg_cm"]["pass"] is True
    assert margins["torque_kg_cm"]["margin"] == pytest.approx(5.0)


class TestMakeComponentSelector:
    def _selector(self, twin: InMemoryTwinAPI) -> tuple[object, object]:
        decision_recorder = make_decision_recorder(twin)
        component_recorder = make_component_recorder(twin)
        select = make_component_selector(
            twin, decision_recorder=decision_recorder, component_recorder=component_recorder
        )
        return select, component_recorder

    async def test_selects_and_records_a_real_decision_with_alternatives(self) -> None:
        twin = InMemoryTwinAPI.create()
        select, _ = self._selector(twin)

        result = await select(
            candidates=[
                {"mpn": "DS3218", "manufacturer": "Miuzei", "specs": {"torque_kg_cm": 19.6}},
                {"mpn": "MG996R", "manufacturer": "TowerPro", "specs": {"torque_kg_cm": 25.0}},
            ],
            required_specs={"torque_kg_cm": {"op": ">=", "value": 20.0}},
            selected_mpn="MG996R",
            category="servo",
            purchase_unit="cots_assembly",
            title="Elbow joint actuator",
            rationale="MG996R clears the required torque with margin; DS3218 does not.",
        )

        assert result["selected_mpn"] == "MG996R"
        assert result["selected_meets_requirements"] is True
        decision_wp = await twin.get_work_product(UUID(result["decision_node_id"]))
        assert decision_wp is not None
        assert decision_wp.type == WorkProductType.DESIGN_DECISION
        alternatives = decision_wp.metadata["alternatives"]
        assert len(alternatives) == 1
        assert "DS3218" in alternatives[0]["option"]
        assert "FAIL" in alternatives[0]["reason_rejected"]

    async def test_records_selected_candidate_as_a_real_bom_item(self) -> None:
        twin = InMemoryTwinAPI.create()
        select, _ = self._selector(twin)

        result = await select(
            candidates=[
                {"mpn": "MG996R", "manufacturer": "TowerPro", "specs": {"torque_kg_cm": 25.0}},
            ],
            required_specs={"torque_kg_cm": {"op": ">=", "value": 20.0}},
            selected_mpn="MG996R",
            category="servo",
            purchase_unit="cots_assembly",
            title="T",
            rationale="R",
        )
        bom_item = await twin.graph.get_node(UUID(result["node_id"]))
        assert bom_item is not None
        assert bom_item.specifications["category"] == "servo"
        assert bom_item.specifications["torque_kg_cm"] == 25.0

    async def test_links_decision_to_selected_bom_item_via_generated_from(self) -> None:
        twin = InMemoryTwinAPI.create()
        select, _ = self._selector(twin)

        result = await select(
            candidates=[{"mpn": "A", "manufacturer": "M", "specs": {"torque_kg_cm": 25.0}}],
            required_specs={"torque_kg_cm": {"op": ">=", "value": 20.0}},
            selected_mpn="A",
            category="servo",
            purchase_unit="cots_assembly",
            title="T",
            rationale="R",
        )
        edges = await twin.get_edges(
            UUID(result["decision_node_id"]), edge_type=EdgeType.GENERATED_FROM
        )
        assert len(edges) == 1
        assert str(edges[0].target_id) == result["node_id"]

    async def test_selecting_a_candidate_that_fails_a_requirement_is_flagged_not_blocked(
        self,
    ) -> None:
        """The tool computes and reports margins, it doesn't refuse a
        selection -- an engineer may still choose an under-spec part with a
        documented rationale (e.g. reduced duty cycle); the honesty
        requirement is that the failure is visible, not that it's illegal."""
        twin = InMemoryTwinAPI.create()
        select, _ = self._selector(twin)

        result = await select(
            candidates=[
                {"mpn": "DS3218", "manufacturer": "Miuzei", "specs": {"torque_kg_cm": 19.6}}
            ],
            required_specs={"torque_kg_cm": {"op": ">=", "value": 20.0}},
            selected_mpn="DS3218",
            category="servo",
            purchase_unit="cots_assembly",
            title="T",
            rationale="Accepted with reduced duty cycle.",
        )
        assert result["selected_meets_requirements"] is False

    async def test_selected_mpn_must_be_one_of_candidates(self) -> None:
        twin = InMemoryTwinAPI.create()
        select, _ = self._selector(twin)

        with pytest.raises(ValueError, match="selected_mpn"):
            await select(
                candidates=[{"mpn": "A", "manufacturer": "M", "specs": {"torque_kg_cm": 25.0}}],
                required_specs={"torque_kg_cm": {"op": ">=", "value": 20.0}},
                selected_mpn="NOT-A-CANDIDATE",
                category="servo",
                purchase_unit="cots_assembly",
                title="T",
                rationale="R",
            )

    async def test_multiple_required_specs_all_checked(self) -> None:
        twin = InMemoryTwinAPI.create()
        select, _ = self._selector(twin)

        result = await select(
            candidates=[
                {
                    "mpn": "A",
                    "manufacturer": "M",
                    "specs": {"v_in_max": 12.0, "i_out_max": 2.0},
                },
            ],
            required_specs={
                "v_in_max": {"op": "<=", "value": 24.0},
                "i_out_max": {"op": ">=", "value": 1.5},
            },
            selected_mpn="A",
            category="motor_driver",
            purchase_unit="discrete_part",
            title="T",
            rationale="R",
        )
        assert result["selected_meets_requirements"] is True
        assert result["candidates"][0]["margins"]["v_in_max"]["pass"] is True
        assert result["candidates"][0]["margins"]["i_out_max"]["pass"] is True


class TestSelectComponentAdapter:
    async def test_tool_registered_and_returns_shape(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        component_recorder = make_component_recorder(twin)
        selector = make_component_selector(
            twin, decision_recorder=decision_recorder, component_recorder=component_recorder
        )
        server = TwinServer(twin=twin, component_selector=selector)
        assert "twin.select_component" in server.tool_ids

        out = await server.select_component(
            {
                "candidates": [{"mpn": "A", "manufacturer": "M", "specs": {"torque_kg_cm": 25.0}}],
                "required_specs": {"torque_kg_cm": {"op": ">=", "value": 20.0}},
                "selected_mpn": "A",
                "category": "servo",
                "purchase_unit": "cots_assembly",
                "title": "T",
                "rationale": "R",
            }
        )
        assert out["selected_mpn"] == "A"

    def test_not_registered_when_none_supplied(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.select_component" not in server.tool_ids

    async def test_missing_candidates_rejected(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        component_recorder = make_component_recorder(twin)
        selector = make_component_selector(
            twin, decision_recorder=decision_recorder, component_recorder=component_recorder
        )
        server = TwinServer(twin=twin, component_selector=selector)
        with pytest.raises(ValueError, match="candidates"):
            await server.select_component(
                {
                    "required_specs": {"torque_kg_cm": {"op": ">=", "value": 20.0}},
                    "selected_mpn": "A",
                    "category": "servo",
                    "purchase_unit": "cots_assembly",
                    "title": "T",
                    "rationale": "R",
                }
            )

    async def test_missing_required_specs_rejected(self) -> None:
        twin = InMemoryTwinAPI.create()
        decision_recorder = make_decision_recorder(twin)
        component_recorder = make_component_recorder(twin)
        selector = make_component_selector(
            twin, decision_recorder=decision_recorder, component_recorder=component_recorder
        )
        server = TwinServer(twin=twin, component_selector=selector)
        with pytest.raises(ValueError, match="required_specs"):
            await server.select_component(
                {
                    "candidates": [
                        {"mpn": "A", "manufacturer": "M", "specs": {"torque_kg_cm": 25.0}}
                    ],
                    "selected_mpn": "A",
                    "category": "servo",
                    "purchase_unit": "cots_assembly",
                    "title": "T",
                    "rationale": "R",
                }
            )


class TestComponentSelectionRoutes:
    @pytest.fixture
    def twin(self) -> InMemoryTwinAPI:
        return InMemoryTwinAPI.create()

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.component_selection.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture(autouse=True)
    def _wire(self, twin: InMemoryTwinAPI):
        from api_gateway.component_selection.routes import init_component_selector

        decision_recorder = make_decision_recorder(twin)
        component_recorder = make_component_recorder(twin)
        selector = make_component_selector(
            twin, decision_recorder=decision_recorder, component_recorder=component_recorder
        )
        init_component_selector(selector)
        yield
        init_component_selector(None)

    async def test_select_round_trip(self, client) -> None:
        async with client:
            resp = await client.post(
                "/v1/component-selection/select",
                json={
                    "candidates": [
                        {
                            "mpn": "DS3218",
                            "manufacturer": "Miuzei",
                            "specs": {"torque_kg_cm": 19.6},
                        },
                        {
                            "mpn": "MG996R",
                            "manufacturer": "TowerPro",
                            "specs": {"torque_kg_cm": 25.0},
                        },
                    ],
                    "requiredSpecs": {"torque_kg_cm": {"op": ">=", "value": 20.0}},
                    "selectedMpn": "MG996R",
                    "category": "servo",
                    "purchaseUnit": "cots_assembly",
                    "title": "Elbow joint actuator",
                    "rationale": "MG996R clears the required torque.",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["selected_mpn"] == "MG996R"
        assert body["selected_meets_requirements"] is True

    async def test_selected_mpn_not_in_candidates_400s(self, client) -> None:
        async with client:
            resp = await client.post(
                "/v1/component-selection/select",
                json={
                    "candidates": [
                        {"mpn": "A", "manufacturer": "M", "specs": {"torque_kg_cm": 25.0}}
                    ],
                    "requiredSpecs": {"torque_kg_cm": {"op": ">=", "value": 20.0}},
                    "selectedMpn": "NOT-A-CANDIDATE",
                    "category": "servo",
                    "purchaseUnit": "cots_assembly",
                    "title": "T",
                    "rationale": "R",
                },
            )
        assert resp.status_code == 400

    async def test_selector_unavailable_503s(self, client) -> None:
        from api_gateway.component_selection.routes import init_component_selector

        init_component_selector(None)
        async with client:
            resp = await client.post(
                "/v1/component-selection/select",
                json={
                    "candidates": [
                        {"mpn": "A", "manufacturer": "M", "specs": {"torque_kg_cm": 25.0}}
                    ],
                    "requiredSpecs": {"torque_kg_cm": {"op": ">=", "value": 20.0}},
                    "selectedMpn": "A",
                    "category": "servo",
                    "purchaseUnit": "cots_assembly",
                    "title": "T",
                    "rationale": "R",
                },
            )
        assert resp.status_code == 503
