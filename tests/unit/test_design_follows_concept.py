"""The design builds the concept that was approved (FORGE-763).

The Chair's CAD drifted from its concept with nothing to notice: the design
gate only asked whether a cad_model existed. It now reads the latest selected
assembly and refuses a design that leaves out a part the concept makes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from orchestrator.design_flow.context import (
    DESIGN_PHASE_ID,
    ConceptAssembly,
    assembly_conformance_gap,
    parse_concept_assembly,
)
from orchestrator.design_flow.templates import load_templates

PROJECT = "11111111-1111-1111-1111-111111111111"

CHAIR = parse_concept_assembly(
    {
        "parts": [
            {"name": "Seat"},
            {"name": "Leg", "quantity": 4},
            {"name": "Backrest"},
            {"name": "Dowel 8x40", "source": "standard", "quantity": 8},
        ],
        "connections": [
            {"parts": ["Seat", "Leg"], "joint": "dowel"},
            {"parts": ["Seat", "Backrest"], "joint": "dowel"},
        ],
    }
)


class TestConformance:
    def test_every_made_part_committed_is_no_gap(self) -> None:
        assert assembly_conformance_gap(CHAIR, ["Chair Seat", "Leg 1", "Backrest"]) is None

    def test_a_missing_part_is_named(self) -> None:
        gap = assembly_conformance_gap(CHAIR, ["Chair Seat", "Leg 1"])
        assert gap is not None and "Backrest" in gap and "Seat" not in gap.split(":")[1]

    def test_standard_parts_need_no_cad(self) -> None:
        assert "Dowel" not in (assembly_conformance_gap(CHAIR, []) or "")

    def test_no_concept_means_nothing_to_conform_to(self) -> None:
        assert assembly_conformance_gap(None, []) is None


class _Evaluator:
    def __init__(self, assembly: ConceptAssembly | None, names: list[str]) -> None:
        self.assembly = assembly
        self.names = names

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"cad_model"}

    async def concept_assembly(self, _project: str | None) -> ConceptAssembly | None:
        return self.assembly

    async def cad_part_names(self, _project: str | None) -> list[str]:
        return self.names


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal(evaluator: _Evaluator) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(evaluator, _Constraints(), None)
    phase = {"id": DESIGN_PHASE_ID, "required_deliverables": ["cad_model"]}
    return asyncio.run(checkers.evaluate(phase, PROJECT))


def _in_process(evaluator: _Evaluator) -> Any:
    from orchestrator.design_flow.executor import DesignFlowExecutor, FlowContext, GateCoordinator
    from orchestrator.design_flow.spec import Phase
    from orchestrator.harness.runs import InMemoryRunStore

    executor = DesignFlowExecutor(
        store=InMemoryRunStore(),
        brain=None,
        coordinator=GateCoordinator(),
        gate_evaluator=evaluator,
    )
    phase = Phase(
        id=DESIGN_PHASE_ID, title="t", objective="o", required_deliverables=("cad_model",)
    )
    return asyncio.run(
        executor._readiness(phase, FlowContext(goal="g", project_id=PROJECT), since_ts=0.0)
    )


class TestTheDesignGate:
    def test_a_design_missing_a_concept_part_is_not_ready_in_both_engines(self) -> None:
        evaluator = _Evaluator(CHAIR, ["Seat", "Leg"])
        assert not _temporal(evaluator).ready
        assert any("Backrest" in m for m in _in_process(evaluator).missing)

    def test_a_design_that_builds_the_concept_is_ready_and_says_so(self) -> None:
        check = _temporal(_Evaluator(CHAIR, ["Seat", "Leg", "Backrest"]))
        assert check.ready and "built to the concept" in check.reason
        assert _in_process(_Evaluator(CHAIR, ["Seat", "Leg", "Backrest"])).ready

    def test_without_a_concept_the_gate_is_as_before(self) -> None:
        assert _temporal(_Evaluator(None, ["Bracket"])).ready
        assert _in_process(_Evaluator(None, ["Bracket"])).ready


def test_the_design_brief_names_the_selected_assembly() -> None:
    design = next(p for p in load_templates()["mech_v1"].definition.phases if p.id == "design")
    assert "selected at the concept gate" in design.objective
    assert any("selected concept" in c for c in design.gate.criteria)
