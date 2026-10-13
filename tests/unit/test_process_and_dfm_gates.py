"""Process chosen before design, DFM checked after it (FORGE-611).

The route gate needs the ranking the process was chosen from; the DFM gate
reads the verdict of the review against that process.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.decision_recorder import make_decision_recorder
from orchestrator.design_flow.context import (
    DFM_REVIEW_MISSING,
    DFM_REVIEW_PHASE_ID,
    DfmReview,
    dfm_review_gap,
    parse_dfm_review,
    parse_process_ranking,
)
from twin_core.api import InMemoryTwinAPI

PROJECT = "11111111-1111-1111-1111-111111111111"

_REVIEW = {
    "process": "laser cut + weld",
    "verdict": "manufacturable",
    "findings": {"pass": 4, "fail": 0, "unknown": 0},
    "plan": {
        "stock": ["40x40x2 SHS S235, 4 x 719 mm"],
        "operations": ["cut legs", "weld caps", "fix top"],
        "quantity": 4,
    },
}


class TestTheRanking:
    def test_a_list_or_an_object_is_accepted(self) -> None:
        candidates = [{"process": "laser cut + weld", "reasons": ["fits"], "cost_at_quantity": 460}]
        assert parse_process_ranking(candidates).candidates[0].cost_at_quantity == 460
        ranking = parse_process_ranking(
            {"candidates": candidates, "recommended": "laser cut + weld"}
        )
        assert ranking.recommended == "laser cut + weld"
        assert "1. laser cut + weld" in ranking.summary()

    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            ("laser", "must be an object"),
            ({"candidates": []}, "at least one candidate"),
            ([{"reasons": ["x"]}], "needs a process"),
            ([{"process": "cnc", "reasons": []}], "needs its reasons"),
        ],
    )
    def test_anything_else_is_refused(self, value: Any, fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment):
            parse_process_ranking(value)


class TestTheReview:
    def test_a_full_review_parses(self) -> None:
        review = parse_dfm_review(_REVIEW)
        assert review.verdict == "manufacturable" and review.passed == 4
        assert review.operations == ("cut legs", "weld caps", "fix top")
        assert dfm_review_gap(review) is None

    @pytest.mark.parametrize(
        ("change", "fragment"),
        [
            ({"process": ""}, "needs the process"),
            ({"verdict": "probably"}, "verdict must be one of"),
            ({"plan": {"operations": []}}, "needs the operations"),
            (
                {"plan": {"operations": ["cut"], "unit_cost_estimate": 50}},
                "needs its cost_source",
            ),
            ({"findings": {"fail": -1}}, "whole number"),
        ],
    )
    def test_a_bad_review_is_refused(self, change: dict[str, Any], fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment):
            parse_dfm_review({**_REVIEW, **change})

    def test_a_failed_review_needs_no_plan_and_blocks(self) -> None:
        review = parse_dfm_review(
            {"process": "cnc", "verdict": "not_manufacturable", "findings": {"fail": 2}}
        )
        gap = dfm_review_gap(review)
        assert gap is not None and "2 failing feature(s)" in gap

    def test_incomplete_passes_and_names_what_is_missing(self) -> None:
        review = parse_dfm_review(
            {**_REVIEW, "verdict": "incomplete", "unknowns": ["hole_diameter: no sourced limit"]}
        )
        assert dfm_review_gap(review) is None
        assert "hole_diameter" in review.summary()

    def test_no_review_is_a_gap(self) -> None:
        assert dfm_review_gap(None) == DFM_REVIEW_MISSING


class TestTheRecorder:
    def test_ranking_and_review_are_stored(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_decision_recorder(twin)
        out = asyncio.run(
            record(
                title="Table DFM review",
                rationale="Checked against laser cut + weld.",
                dfm_review=_REVIEW,
                process_ranking=[{"process": "laser cut + weld", "reasons": ["fits"]}],
            )
        )
        meta = asyncio.run(twin.get_work_product(UUID(out["node_id"]))).metadata
        assert meta["dfm_review"]["verdict"] == "manufacturable"
        assert meta["process_ranking"]["candidates"][0]["process"] == "laser cut + weld"

    def test_a_malformed_review_is_refused(self) -> None:
        record = make_decision_recorder(InMemoryTwinAPI.create())
        with pytest.raises(ValueError, match="verdict must be one of"):
            asyncio.run(
                record(title="x", rationale="y", dfm_review={"process": "p", "verdict": "?"})
            )


class _Evaluator:
    def __init__(self, review: DfmReview | None) -> None:
        self.review = review

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"design_decision"}

    async def route_decision(self, _project: str | None) -> None:
        return None

    async def dfm_review(self, _project: str | None) -> DfmReview | None:
        return self.review


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal(review: DfmReview | None, phase_id: str = DFM_REVIEW_PHASE_ID) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(_Evaluator(review), _Constraints(), None)
    phase = {"id": phase_id, "required_deliverables": ["design_decision"]}
    return asyncio.run(checkers.evaluate(phase, PROJECT))


def _in_process(review: DfmReview | None, phase_id: str = DFM_REVIEW_PHASE_ID) -> Any:
    from orchestrator.design_flow.executor import DesignFlowExecutor, FlowContext, GateCoordinator
    from orchestrator.design_flow.spec import Phase
    from orchestrator.harness.runs import InMemoryRunStore

    executor = DesignFlowExecutor(
        store=InMemoryRunStore(),
        brain=None,
        coordinator=GateCoordinator(),
        gate_evaluator=_Evaluator(review),
    )
    phase = Phase(id=phase_id, title="t", objective="o", required_deliverables=("design_decision",))
    return asyncio.run(
        executor._readiness(phase, FlowContext(goal="g", project_id=PROJECT), since_ts=0.0)
    )


_FAILED = DfmReview(process="cnc", verdict="not_manufacturable", failed=2)
_INCOMPLETE = DfmReview(
    process="laser cut + weld", verdict="incomplete", unknowns=("hole_diameter: no limit",)
)


class TestTheDfmGate:
    def test_no_review_is_not_ready_in_both_engines(self) -> None:
        assert DFM_REVIEW_MISSING in _temporal(None).missing
        assert DFM_REVIEW_MISSING in _in_process(None).missing

    def test_a_failed_review_is_not_ready_in_both_engines(self) -> None:
        assert not _temporal(_FAILED).ready
        assert not _in_process(_FAILED).ready

    def test_incomplete_is_ready_and_says_approving_accepts_it(self) -> None:
        check = _temporal(_INCOMPLETE)
        assert check.ready
        assert "DFM incomplete" in check.reason and "approving accepts it" in check.reason
        assert _in_process(_INCOMPLETE).ready

    def test_manufacturable_is_ready_and_named(self) -> None:
        check = _temporal(parse_dfm_review(_REVIEW))
        assert check.ready and "DFM manufacturable for laser cut + weld" in check.reason

    def test_hardware_v1_manufacturing_phase_is_untouched(self) -> None:
        assert _temporal(None, phase_id="manufacturing").ready
        assert _in_process(None, phase_id="manufacturing").ready


class _FlowEvaluator(_Evaluator):
    """Everything present, a ranked route chosen, and the given DFM review."""

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"intent", "stakeholder_need", "design_decision", "constraint_set", "cad_model"}

    async def route_decision(self, _project: str | None) -> Any:
        from orchestrator.design_flow.context import RouteDecision

        return RouteDecision(
            route="vendor",
            process="laser cut + weld",
            material_class="steel",
            ranking=parse_process_ranking([{"process": "laser cut + weld", "reasons": ["fits"]}]),
        )


@pytest.mark.asyncio
async def test_a_mech_v1_run_chooses_the_process_first_and_stops_on_a_failed_dfm() -> None:
    from orchestrator.design_flow.executor import (
        DesignFlowExecutor,
        FlowContext,
        GateCoordinator,
        PhaseOutcome,
    )
    from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus

    class _Brain:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def run_phase(self, *, goal: str, phase: Any, context: FlowContext) -> PhaseOutcome:
            self.seen.append(phase.id)
            return PhaseOutcome(summary=f"did {phase.id}", artifacts=[], status="completed")

    async def wait_for_gate(store: Any, run_id: str) -> None:
        for _ in range(400):
            if store.get(run_id).status is RunStatus.AWAITING_APPROVAL:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"stuck at {store.get(run_id).status}")

    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = _Brain()
    run = store.create({"goal": "a table", "flow": "mech_v1", "project_id": PROJECT})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, gate_evaluator=_FlowEvaluator(_FAILED)
    )
    task = asyncio.create_task(executor.run(run.id))
    for _ in range(6):  # intent .. route_selection, design
        await wait_for_gate(store, run.id)
        store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await wait_for_gate(store, run.id)
    # The process was chosen before the design, and the DFM gate held the run.
    assert brain.seen.index("route_selection") < brain.seen.index("design")
    assert brain.seen[-1] == "dfm_review"
    reason = store.get(run.id).approval_reason or ""
    assert "NOT READY" in reason and "failing feature" in reason
    store.submit_approval(run.id, ApprovalDecision.REJECT)
    await asyncio.wait_for(task, timeout=2.0)
    assert "simulation" not in brain.seen
