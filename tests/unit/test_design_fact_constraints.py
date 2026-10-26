"""Structured requirements measured against the design's records (FORGE-698).

An electronics run recorded 16 structured requirements (board size, layers,
DRC and ERC errors, build errors, ...). Each was stored with the placeholder
expression ``True``; the engine counted them as evaluated and the requirement
matrix showed every one as no_data, so nothing was ever compared to the board.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from api_gateway.requirement_intelligence.matrix import build_requirement_matrix
from api_gateway.runs.design_fact_constraints import (
    check_design_fact_constraints,
    facts_from_records,
    measure,
    read_design_facts,
)
from api_gateway.runs.gate_eval import TwinConstraintChecker
from twin_core.api import InMemoryTwinAPI
from twin_core.constraint_engine.validator import is_placeholder_bound
from twin_core.items.service import commit_revision, plan_revision
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct

PCB_FACTS = {
    "board_length_mm": 40,
    "board_width_mm": 40,
    "copper_layer_count": 4,
    "drc_error_count": 2,
    "unrouted_count": 0,
    "file_count": 4,
}


def _req(metric: str, operator: str, limit: float, unit: str = "", name: str = "") -> Constraint:
    return Constraint(
        name=name or metric,
        expression="True",
        severity=ConstraintSeverity.ERROR,
        domain="electronics",
        source="test",
        metric=metric,
        operator=operator,
        limit=limit,
        unit=unit,
    )


def _facts() -> dict:
    return facts_from_records(
        [
            (
                "pcb-1",
                "pcb_layout",
                "Routed PCB",
                {**PCB_FACTS, "item_key": "PCB-B", "item_revision": 1},
            ),
            (
                "sch-1",
                "schematic",
                "Schematic",
                {"erc_errors": 0, "item_key": "SCH-B", "item_revision": 1},
            ),
        ]
    )


class TestMeasure:
    def test_pass_names_the_record_and_the_value(self) -> None:
        out = measure(_req("board_length_mm", "<=", 40, "mm"), _facts())
        assert out is not None and out.status == "pass"
        assert out.node_id == "pcb-1"
        assert "board_length_mm = 40 on pcb_layout 'Routed PCB' (PCB-B@1)" in out.detail

    def test_fail_states_the_numbers(self) -> None:
        out = measure(_req("drc_error_count", "==", 0), _facts())
        assert out is not None and out.status == "fail"
        assert "drc_error_count = 2" in out.detail and "== 0" in out.detail

    def test_alias_finds_the_tool_name_for_a_fact(self) -> None:
        out = measure(_req("erc_error_count", "==", 0), _facts())
        assert out is not None and out.status == "pass"
        assert out.node_id == "sch-1"

    def test_a_metric_no_record_states_is_not_evaluated_never_passed(self) -> None:
        out = measure(_req("supply_current_ma", "<=", 150, "mA"), _facts())
        assert out is not None and out.status == "not_evaluated"
        assert "supply_current_ma" in out.detail

    def test_bookkeeping_numbers_are_not_facts(self) -> None:
        out = measure(_req("file_count", "<=", 10), _facts())
        assert out is not None and out.status == "not_evaluated"

    def test_a_requirement_without_a_binding_is_not_measured(self) -> None:
        free = Constraint(
            name="free",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="x",
            source="t",
        )
        assert measure(free, _facts()) is None


def test_gate_check_counts_pass_fail_and_not_evaluated() -> None:
    reqs = [
        _req("board_length_mm", "<=", 40, "mm"),
        _req("drc_error_count", "==", 0),
        _req("supply_current_ma", "<=", 150, "mA"),
    ]
    out = check_design_fact_constraints(reqs, _facts())
    assert out.evaluated == 2
    assert len(out.violations) == 1 and out.violations[0].startswith("drc_error_count:")
    assert len(out.not_evaluated) == 1 and out.not_evaluated[0].startswith("supply_current_ma:")


def test_placeholder_only_counts_with_a_binding() -> None:
    assert is_placeholder_bound(_req("board_length_mm", "<=", 40, "mm"))
    plain = Constraint(
        name="p", expression="True", severity=ConstraintSeverity.ERROR, domain="x", source="t"
    )
    assert not is_placeholder_bound(plain)


# ---------------------------------------------------------------- with a twin


async def _commit_pcb(twin: InMemoryTwinAPI, project_id: UUID, facts: dict) -> UUID:
    plan = await plan_revision(
        twin, item_type="pcb_layout", name="Routed PCB", project_id=project_id, author="t"
    )
    wp = await twin.create_work_product(
        WorkProduct(
            name="Routed PCB",
            type=WorkProductType.PCB_LAYOUT,
            domain="electronics",
            file_path="",
            content_hash=str(uuid4()),
            format="kicad_pcb",
            created_by="test",
            project_id=project_id,
            metadata={**facts, **plan.stamp()},
        )
    )
    await commit_revision(twin, plan, wp.id, name="Routed PCB")
    return wp.id


async def _seed(twin: InMemoryTwinAPI, project_id: UUID, req: Constraint) -> Constraint:
    req.project_id = project_id
    return await twin.create_constraint(req)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def test_matrix_measures_a_requirement_with_no_claim(twin) -> None:
    project_id = uuid4()
    pcb = await _commit_pcb(twin, project_id, PCB_FACTS)
    await _seed(twin, project_id, _req("board_length_mm", "<=", 40, "mm"))
    await _seed(twin, project_id, _req("drc_error_count", "==", 0))
    await _seed(twin, project_id, _req("supply_current_ma", "<=", 150, "mA"))

    rows = {r.requirementName: r for r in await build_requirement_matrix(twin, project_id)}

    assert rows["board_length_mm"].status == "pass"
    assert rows["board_length_mm"].artefactIds == [str(pcb)]
    assert rows["board_length_mm"].detail.startswith("measured from the design:")
    assert rows["drc_error_count"].status == "fail"
    assert rows["supply_current_ma"].status == "no_data"
    assert "no current design record" in rows["supply_current_ma"].detail


async def test_only_the_current_revision_is_read(twin) -> None:
    project_id = uuid4()
    await _commit_pcb(twin, project_id, {**PCB_FACTS, "drc_error_count": 2})
    await _commit_pcb(twin, project_id, {**PCB_FACTS, "drc_error_count": 0})
    facts = await read_design_facts(twin, project_id)
    assert facts["drc_error_count"].value == 0


class _Backend:
    def __init__(self, ids: list[UUID]) -> None:
        self._ids = ids

    async def get_project(self, project_id: str) -> object:
        return SimpleNamespace(work_products=[SimpleNamespace(id=i) for i in self._ids])


async def test_gate_fails_on_a_measured_violation_and_skips_the_placeholder(twin) -> None:
    project_id = uuid4()
    pcb = await _commit_pcb(twin, project_id, PCB_FACTS)
    await _seed(twin, project_id, _req("board_length_mm", "<=", 40, "mm"))
    await _seed(twin, project_id, _req("drc_error_count", "==", 0))

    report = await TwinConstraintChecker(twin, _Backend([pcb])).check(str(project_id))

    assert not report.passed
    assert any(v.startswith("drc_error_count:") for v in report.violations)
    # Two measured, none counted from the placeholder expressions.
    assert report.evaluated_count == 2
    # A board's length is read from the PCB, not reported as "no cad_model".
    assert report.not_evaluated == []
