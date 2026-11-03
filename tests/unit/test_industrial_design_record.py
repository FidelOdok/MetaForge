"""Industrial design as constraints and evidence, with no decision left open (FORGE-764).

The Chair's industrial design decision was prose: the joint and edge language
never reached the design, ergonomics and finishes were asserted without the
checks behind them, and open questions passed the gate.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.decision_recorder import make_decision_recorder
from orchestrator.design_flow.context import (
    INDUSTRIAL_DESIGN_MISSING,
    INDUSTRIAL_DESIGN_PHASE_ID,
    IndustrialDesignRecord,
    industrial_design_gap,
    parse_industrial_design,
)
from orchestrator.design_flow.templates import load_templates
from twin_core.api import InMemoryTwinAPI

PROJECT = "11111111-1111-1111-1111-111111111111"

RECORD: dict[str, Any] = {
    "joint_language": "exposed dowels, flush with the seat face",
    "edge_treatment": "R3 on every hand-contact edge, R1 elsewhere",
    "ergonomics": {"verdict": "pass", "source": "BS EN 1335-1 table 1, seat height"},
    "finishes": [{"part": "Seat", "appearance": "clear matt lacquer", "compatible": True}],
    "open_decisions": [],
}


class TestTheRecord:
    def test_a_full_record_parses(self) -> None:
        record = parse_industrial_design(RECORD)
        assert record.joint_language.startswith("exposed dowels")
        assert record.as_metadata()["ergonomics"] == {
            "verdict": "pass",
            "source": "BS EN 1335-1 table 1, seat height",
        }
        assert industrial_design_gap(record) is None

    def test_not_applicable_needs_its_reason(self) -> None:
        record = parse_industrial_design(
            {**RECORD, "ergonomics": {"verdict": "not_applicable", "reason": "never handled"}}
        )
        assert record.as_metadata()["ergonomics"]["reason"] == "never handled"
        with pytest.raises(ValueError, match="reason"):
            parse_industrial_design({**RECORD, "ergonomics": {"verdict": "not_applicable"}})

    @pytest.mark.parametrize(
        ("change", "fragment"),
        [
            ({"joint_language": ""}, "joint_language"),
            ({"edge_treatment": None}, "edge_treatment"),
            ({"ergonomics": {"verdict": "pass"}}, "source"),
            ({"ergonomics": {"verdict": "fine", "source": "x"}}, "verdict"),
            ({"finishes": [{"part": "Seat", "appearance": "oil"}]}, "compatible"),
        ],
    )
    def test_a_bad_record_is_refused(self, change: dict[str, Any], fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment):
            parse_industrial_design({**RECORD, **change})


class TestTheGap:
    def test_no_record_is_a_gap(self) -> None:
        assert industrial_design_gap(None) == INDUSTRIAL_DESIGN_MISSING

    def test_an_open_decision_blocks(self) -> None:
        gap = industrial_design_gap(
            parse_industrial_design({**RECORD, "open_decisions": ["backrest angle"]})
        )
        assert gap is not None and "backrest angle" in gap

    def test_a_failed_ergonomics_check_blocks(self) -> None:
        gap = industrial_design_gap(
            parse_industrial_design({**RECORD, "ergonomics": {"verdict": "fail", "source": "s"}})
        )
        assert gap is not None and "ergonomics" in gap

    def test_an_incompatible_finish_blocks(self) -> None:
        finishes = [{"part": "Leg", "appearance": "powder coat", "compatible": False}]
        gap = industrial_design_gap(parse_industrial_design({**RECORD, "finishes": finishes}))
        assert gap is not None and "Leg" in gap

    def test_no_finish_at_all_blocks(self) -> None:
        assert industrial_design_gap(parse_industrial_design({**RECORD, "finishes": []}))


@pytest.mark.asyncio
async def test_the_decision_stores_it_and_refuses_a_bad_one() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_decision_recorder(twin)
    out = await record(title="Industrial design", rationale="r", industrial_design=RECORD)
    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.metadata["industrial_design"]["edge_treatment"].startswith("R3")
    with pytest.raises(ValueError, match="twin.record_decision: industrial_design"):
        await record(title="ID 2", rationale="r", industrial_design={"joint_language": "x"})


class _Evaluator:
    def __init__(self, record: IndustrialDesignRecord | None) -> None:
        self.record = record

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"design_decision"}

    async def industrial_design(self, _project: str | None) -> IndustrialDesignRecord | None:
        return self.record


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal(record: IndustrialDesignRecord | None) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(_Evaluator(record), _Constraints(), None)
    phase = {"id": INDUSTRIAL_DESIGN_PHASE_ID, "required_deliverables": ["design_decision"]}
    return asyncio.run(checkers.evaluate(phase, PROJECT))


def _in_process(record: IndustrialDesignRecord | None) -> Any:
    from orchestrator.design_flow.executor import DesignFlowExecutor, FlowContext, GateCoordinator
    from orchestrator.design_flow.spec import Phase
    from orchestrator.harness.runs import InMemoryRunStore

    executor = DesignFlowExecutor(
        store=InMemoryRunStore(),
        brain=None,
        coordinator=GateCoordinator(),
        gate_evaluator=_Evaluator(record),
    )
    phase = Phase(
        id=INDUSTRIAL_DESIGN_PHASE_ID,
        title="t",
        objective="o",
        required_deliverables=("design_decision",),
    )
    return asyncio.run(
        executor._readiness(phase, FlowContext(goal="g", project_id=PROJECT), since_ts=0.0)
    )


class TestTheGate:
    def test_an_open_decision_is_not_ready_in_both_engines(self) -> None:
        record = parse_industrial_design({**RECORD, "open_decisions": ["colour"]})
        assert not _temporal(record).ready
        assert not _in_process(record).ready

    def test_a_settled_record_is_ready_and_summarised(self) -> None:
        check = _temporal(parse_industrial_design(RECORD))
        assert check.ready and "joints: exposed dowels" in check.reason
        assert _in_process(parse_industrial_design(RECORD)).ready


def test_the_brief_asks_for_joint_and_edge_language() -> None:
    phase = next(
        p for p in load_templates()["mech_v1"].definition.phases if p.id == "industrial_design"
    )
    assert "Joint and edge language" in phase.objective
    assert "open_decisions" in phase.objective
    assert any("left open" in c for c in phase.gate.criteria)
