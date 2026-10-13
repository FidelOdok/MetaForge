"""The route-selection gate needs a route, or an accepted deferral (FORGE-608).

The Table run's route-selection phase recorded "route NOT SELECTED
(deferred)" as free text, its gate passed, and the design went ahead on
assumed materials with nothing saying so. A decision now carries a typed
``manufacturing_route`` and the gate reads it.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.decision_recorder import make_decision_recorder
from orchestrator.design_flow.context import (
    PROCESS_RANKING_MISSING,
    ROUTE_DECISION_MISSING,
    ProcessRanking,
    RankedCandidate,
    RouteDecision,
    parse_route_decision,
    route_decision_gap,
)
from orchestrator.design_flow.generator import ROUTE_SELECTION_PHASE_ID
from twin_core.api import InMemoryTwinAPI

_RANKING = ProcessRanking(
    candidates=(
        RankedCandidate(
            process="laser cut + weld", reasons=("fits; cost 460 at 4",), verdict="fits"
        ),
    ),
    recommended="laser cut + weld",
)


class TestTheRule:
    def test_a_chosen_route_with_process_and_material(self) -> None:
        decision = parse_route_decision(
            {"route": "vendor", "process": "laser cut + weld", "material_class": "steel"}
        )
        assert decision == RouteDecision(
            route="vendor", process="laser cut + weld", material_class="steel"
        )
        # FORGE-611: a chosen route also needs the ranking it was chosen from.
        assert route_decision_gap(decision) == PROCESS_RANKING_MISSING
        ranked = replace(decision, ranking=_RANKING)
        assert route_decision_gap(ranked) is None

    def test_a_deferral_with_a_reason(self) -> None:
        decision = parse_route_decision({"deferred": True, "reason": "quantity unknown"})
        assert decision.deferred and decision.reason == "quantity unknown"
        assert route_decision_gap(decision) is None
        assert "DEFERRED" in decision.summary()

    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            ("vendor", "must be an object"),
            ({"route": "undecided", "process": "x", "material_class": "y"}, "in_house' or 'vendor"),
            ({"route": "vendor", "process": "x"}, "process and material_class"),
            ({"route": "in_house", "material_class": "steel"}, "process and material_class"),
            ({"deferred": True}, "needs a reason"),
            ({"deferred": True, "reason": "  "}, "needs a reason"),
        ],
    )
    def test_anything_else_is_refused(self, value: Any, fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment):
            parse_route_decision(value)

    def test_no_decision_is_a_gap(self) -> None:
        assert route_decision_gap(None) == ROUTE_DECISION_MISSING


class TestTheRecorder:
    def test_it_stores_the_route_on_the_decision(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_decision_recorder(twin)
        out = asyncio.run(
            record(
                title="Table v1 manufacturing route",
                rationale="Vendor fabrication suits a one-off steel frame.",
                manufacturing_route={
                    "route": "vendor",
                    "process": "cut and weld",
                    "material_class": "steel",
                },
            )
        )
        node = asyncio.run(twin.get_work_product(UUID(out["node_id"])))
        assert node.metadata["manufacturing_route"] == {
            "route": "vendor",
            "process": "cut and weld",
            "material_class": "steel",
        }

    def test_a_decision_that_names_no_route_is_refused(self) -> None:
        record = make_decision_recorder(InMemoryTwinAPI.create())
        with pytest.raises(ValueError, match="in_house' or 'vendor"):
            asyncio.run(
                record(
                    title="Route",
                    rationale="Not selected",
                    manufacturing_route={"route": "undecided"},
                )
            )


class _Evaluator:
    def __init__(self, decision: RouteDecision | None) -> None:
        self.decision = decision

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"design_decision"}

    async def route_decision(self, _project: str | None) -> RouteDecision | None:
        return self.decision


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal_check(
    decision: RouteDecision | None, phase_id: str = ROUTE_SELECTION_PHASE_ID
) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(_Evaluator(decision), _Constraints(), None)
    phase = {"id": phase_id, "required_deliverables": ["design_decision"]}
    return asyncio.run(checkers.evaluate(phase, "11111111-1111-1111-1111-111111111111"))


class TestTheTemporalGate:
    def test_no_route_decision_is_not_ready(self) -> None:
        check = _temporal_check(None)
        assert not check.ready
        assert ROUTE_DECISION_MISSING in check.missing

    def test_a_chosen_route_is_ready_and_named(self) -> None:
        check = _temporal_check(
            RouteDecision(route="vendor", process="weld", material_class="steel", ranking=_RANKING)
        )
        assert check.ready
        assert "route vendor" in check.reason
        assert "ranking: 1. laser cut + weld" in check.reason

    def test_a_chosen_route_without_its_ranking_is_not_ready(self) -> None:
        check = _temporal_check(
            RouteDecision(route="vendor", process="weld", material_class="steel")
        )
        assert not check.ready
        assert PROCESS_RANKING_MISSING in check.missing

    def test_a_deferral_is_ready_and_says_approving_accepts_it(self) -> None:
        check = _temporal_check(RouteDecision(deferred=True, reason="quantity unknown"))
        assert check.ready
        assert "DEFERRED: quantity unknown" in check.reason
        assert "approving accepts the deferral" in check.reason

    def test_other_phases_are_untouched(self) -> None:
        check = _temporal_check(None, phase_id="design")
        assert check.ready
        assert ROUTE_DECISION_MISSING not in check.missing


class TestTheInProcessGate:
    def _readiness(self, decision: RouteDecision | None, phase_id: str) -> Any:
        from orchestrator.design_flow.executor import (
            DesignFlowExecutor,
            FlowContext,
            GateCoordinator,
        )
        from orchestrator.design_flow.spec import Phase
        from orchestrator.harness.runs import InMemoryRunStore

        executor = DesignFlowExecutor(
            store=InMemoryRunStore(),
            brain=None,
            coordinator=GateCoordinator(),
            gate_evaluator=_Evaluator(decision),
        )
        phase = Phase(
            id=phase_id,
            title="t",
            objective="o",
            required_deliverables=("design_decision",),
        )
        ctx = FlowContext(goal="g", project_id="11111111-1111-1111-1111-111111111111")
        return asyncio.run(executor._readiness(phase, ctx, since_ts=0.0))

    def test_no_route_decision_is_not_ready(self) -> None:
        report = self._readiness(None, ROUTE_SELECTION_PHASE_ID)
        assert not report.ready
        assert ROUTE_DECISION_MISSING in report.missing

    def test_a_deferral_is_ready(self) -> None:
        decision = RouteDecision(deferred=True, reason="later")
        assert self._readiness(decision, ROUTE_SELECTION_PHASE_ID).ready

    def test_other_phases_are_untouched(self) -> None:
        assert self._readiness(None, "design").ready
