"""Claims graded against numbers, and measuring tool results read as facts (FORGE-701).

A sandbox run passed every requirement on a claim whose evidence carried no
value, limit or margin ("claim supported by current evidence (no extractable
margin to grade further)"). After FORGE-698 a later run measured seven
requirements from the design records, but the other seven stayed no_data
although the emulated boot (7/7), the power budget (12.47 mA against 150 mA)
and the parts' datasheet ratings were all recorded as evidence.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.requirement_intelligence.matrix import build_requirement_matrix
from api_gateway.requirement_intelligence.promotion import _MATRIX_STATUS_TO_DECISION
from api_gateway.runs.design_fact_constraints import (
    facts_from_evidence,
    how_to_close,
    measure,
    measures_the_design,
    read_design_facts,
)
from api_gateway.runs.gate_eval import TwinConstraintChecker
from api_gateway.twin.claim_recorder import make_claim_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from orchestrator.design_flow.lifecycle import RequirementStatus, normalize_requirement_status
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.maturity_gate import SATISFIED_DECISIONS
from twin_core.models.work_product import WorkProduct

BOOT = {
    "evidence_type": "simulation",
    "producer": {"tool": "firmware.run_emulated (Renode)", "version": "1.17.0"},
    "inputs": {"board": "disco_f407vg (stand-in for the F411)"},
    "result": {"verdict": "pass", "checks_passed": 7, "boot_checks_failed": 0},
}
BUDGET = {
    "evidence_type": "calculation",
    "producer": {"tool": "power.check_budget"},
    "inputs": {"derating": 0.8},
    "result": {
        "verdict": "pass",
        "vbus_load_ma": 12.466,
        "rail_3v3_load_ma": 12.386,
        "rail_load_margin_pct": 4744,
        "basis": "typical datasheet figures",
    },
}
RATINGS = {
    "evidence_type": "datasheet",
    "producer": {"tool": "web.fetch of manufacturer datasheets"},
    "inputs": {},
    "result": {"rated_ambient_min": -40, "rated_ambient_max": 85},
}
FEA = {
    "evidence_type": "simulation",
    "producer": {"tool": "calculix.run_fea"},
    "inputs": {"load_case": "assumed 50 N"},
    "result": {"verdict": "pass", "max_stress_mpa": 40.0, "boot_checks_failed": 0},
}


def _req(metric: str, operator: str, limit: float, unit: str = "") -> Constraint:
    return Constraint(
        name=metric,
        expression="True",
        severity=ConstraintSeverity.ERROR,
        domain="electronics",
        source="test",
        metric=metric,
        operator=operator,
        limit=limit,
        unit=unit,
    )


class TestFactsFromEvidence:
    def test_measuring_tools_and_datasheets_are_read(self) -> None:
        facts = facts_from_evidence([("boot-1", BOOT), ("pwr-1", BUDGET), ("ds-1", RATINGS)])
        assert facts["boot_checks_failed"].value == 0
        assert facts["vbus_load_ma"].node_id == "pwr-1"
        assert facts["rated_ambient_min"].value == -40
        assert all(f.origin == "evidence" for f in facts.values())

    def test_the_basis_is_kept_with_the_number(self) -> None:
        facts = facts_from_evidence([("boot-1", BOOT), ("pwr-1", BUDGET)])
        boot = facts["boot_checks_failed"].record
        assert "firmware.run_emulated (Renode) 1.17.0" in boot
        assert "board: disco_f407vg (stand-in for the F411)" in boot
        assert "basis: typical datasheet figures" in facts["vbus_load_ma"].record

    def test_an_analysis_resting_on_load_cases_is_not_read(self) -> None:
        assert not measures_the_design(FEA)
        assert facts_from_evidence([("fea-1", FEA)]) == {}

    def test_but_a_claim_vouches_for_what_it_cites(self) -> None:
        assert facts_from_evidence([("fea-1", FEA)], any_tool=True)["max_stress_mpa"].value == 40

    def test_a_result_that_established_nothing_is_skipped(self) -> None:
        partial = {
            **BUDGET,
            "result": {"verdict": "not_established", "vbus_load_ma_known": 12.4},
        }
        assert facts_from_evidence([("pwr-0", partial)]) == {}

    def test_the_first_record_wins(self) -> None:
        older = {**BOOT, "result": {"verdict": "fail", "boot_checks_failed": 3}}
        facts = facts_from_evidence([("new", BOOT), ("old", older)])
        assert facts["boot_checks_failed"].node_id == "new"


class TestMeasureFromEvidence:
    def test_power_metrics_find_the_budget_under_its_own_names(self) -> None:
        facts = facts_from_evidence([("pwr-1", BUDGET)])
        for metric, op, limit in (
            ("vbus_load_current", "<=", 150),
            ("total_supply_current", "<=", 150),
            ("rail_3v3_load_current", "<=", 500),
            ("rail_load_margin_pct", ">=", 20),
        ):
            out = measure(_req(metric, op, limit), facts)
            assert out is not None and out.status == "pass", metric

    def test_a_failed_boot_check_fails_the_requirement(self) -> None:
        failed = {**BOOT, "result": {"verdict": "fail", "boot_checks_failed": 2}}
        out = measure(_req("boot_checks_failed", "==", 0), facts_from_evidence([("b", failed)]))
        assert out is not None and out.status == "fail"
        assert "boot_checks_failed = 2" in out.detail

    def test_not_evaluated_names_the_tool_call_that_closes_it(self) -> None:
        out = measure(_req("boot_checks_failed", "==", 0), {})
        assert out is not None and out.status == "not_evaluated"
        assert "firmware.run_emulated" in out.detail and "twin.record_evidence" in out.detail


def test_how_to_close_matches_metric_families_on_word_starts() -> None:
    assert "kicad.autoroute_pcb" in how_to_close("drc_error_count")
    assert "kicad.generate_schematic" in how_to_close("erc_error_count")
    assert "firmware.build" in how_to_close("flash_used_pct")  # not ERC from "percent"
    assert "power.check_budget" in how_to_close("vbus_load_current")
    assert "datasheet" in how_to_close("rated_ambient_max")
    assert "twin.record_claim" in how_to_close("widget_count")


def test_unverified_is_never_counted_as_a_pass() -> None:
    assert normalize_requirement_status("unverified") is RequirementStatus.INCONCLUSIVE
    assert _MATRIX_STATUS_TO_DECISION["unverified"] not in SATISFIED_DECISIONS


# ---------------------------------------------------------------- with a twin


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _evidence(twin: InMemoryTwinAPI, project_id: UUID, record: dict) -> str:
    out = await make_evidence_recorder(twin)(
        evidence_type=record["evidence_type"],
        producer=record["producer"],
        inputs=record["inputs"],
        result=record["result"],
        project_id=str(project_id),
    )
    return str(out["node_id"])


async def _seed(twin: InMemoryTwinAPI, project_id: UUID, req: Constraint) -> Constraint:
    req.project_id = project_id
    return await twin.create_constraint(req)


async def _artefact(twin: InMemoryTwinAPI, project_id: UUID) -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name="flight sensor firmware",
            type=WorkProductType.FIRMWARE_SOURCE,
            domain="firmware",
            file_path="",
            content_hash=str(uuid4()),
            format="c",
            created_by="test",
            project_id=project_id,
        )
    )


async def _claim(
    twin: InMemoryTwinAPI, project_id: UUID, req: Constraint, evidence: list[str]
) -> None:
    artefact = await _artefact(twin, project_id)
    await make_claim_recorder(twin)(
        requirement_ref=req.name,
        artefact_ref=artefact.name,
        evidence_refs=evidence,
        project_id=str(project_id),
    )


async def test_matrix_reads_the_measuring_tools_with_no_claim(twin) -> None:
    project_id = uuid4()
    await _evidence(twin, project_id, BOOT)
    await _evidence(twin, project_id, BUDGET)
    await _evidence(twin, project_id, RATINGS)
    await _evidence(twin, project_id, FEA)
    await _seed(twin, project_id, _req("boot_checks_failed", "==", 0))
    await _seed(twin, project_id, _req("total_supply_current", "<=", 150, "mA"))
    await _seed(twin, project_id, _req("rated_ambient_min", "<=", -10, "degC"))
    await _seed(twin, project_id, _req("max_stress_mpa", "<=", 100, "MPa"))

    rows = {r.requirementName: r for r in await build_requirement_matrix(twin, project_id)}

    boot = rows["boot_checks_failed"]
    assert boot.status == "pass"
    assert boot.detail.startswith("measured by a tool:")
    assert "stand-in for the F411" in boot.detail
    assert rows["total_supply_current"].status == "pass"
    assert "typical datasheet figures" in rows["total_supply_current"].detail
    assert rows["rated_ambient_min"].status == "pass"
    # The FEA number rests on an assumed load case: it counts only through a claim.
    assert rows["max_stress_mpa"].status == "no_data"
    assert "to close it" in rows["max_stress_mpa"].detail


async def test_a_claim_with_nothing_comparable_is_unverified_not_pass(twin) -> None:
    project_id = uuid4()
    req = await _seed(twin, project_id, _req("total_supply_current", "<=", 150, "mA"))
    bare = await _evidence(
        twin,
        project_id,
        {
            "evidence_type": "calculation",
            "producer": {"tool": "agent.note"},
            "inputs": {},
            "result": {"summary": "well within budget"},
        },
    )
    await _claim(twin, project_id, req, [bare])

    row = (await build_requirement_matrix(twin, project_id))[0]

    assert row.status == "unverified"
    assert "power.check_budget" in row.detail


async def test_a_claim_is_graded_against_the_number_its_evidence_states(twin) -> None:
    project_id = uuid4()
    req = await _seed(twin, project_id, _req("total_supply_current", "<=", 10, "mA"))
    cited = await _evidence(
        twin,
        project_id,
        {
            "evidence_type": "calculation",
            "producer": {"tool": "agent.estimate"},
            "inputs": {},
            "result": {"total_supply_current": 12.5},
        },
    )
    await _claim(twin, project_id, req, [cited])

    row = (await build_requirement_matrix(twin, project_id))[0]

    assert row.status == "fail"
    assert row.detail.startswith("claim graded against the evidence the claim cites:")
    assert "total_supply_current = 12.5" in row.detail


async def test_a_claim_falls_back_to_the_measuring_tools(twin) -> None:
    project_id = uuid4()
    req = await _seed(twin, project_id, _req("boot_checks_failed", "==", 0))
    bare = await _evidence(
        twin,
        project_id,
        {
            "evidence_type": "demonstration",
            "producer": {"tool": "agent.note"},
            "inputs": {},
            "result": {"summary": "it boots"},
        },
    )
    await _evidence(twin, project_id, BOOT)
    await _claim(twin, project_id, req, [bare])

    row = (await build_requirement_matrix(twin, project_id))[0]

    assert row.status == "pass"
    assert row.detail.startswith("claim graded against a measuring tool's result:")


async def test_a_free_text_requirement_keeps_the_bare_claim_pass(twin) -> None:
    project_id = uuid4()
    req = await twin.create_constraint(
        Constraint(
            name="readable silkscreen",
            expression="True",
            message="designators readable",
            severity=ConstraintSeverity.ERROR,
            domain="electronics",
            source="test",
            project_id=project_id,
        )
    )
    note = await _evidence(
        twin,
        project_id,
        {
            "evidence_type": "inspection",
            "producer": {"tool": "review"},
            "inputs": {},
            "result": {"summary": "checked"},
        },
    )
    await _claim(twin, project_id, req, [note])

    row = (await build_requirement_matrix(twin, project_id))[0]

    assert row.status == "pass"


async def test_design_facts_include_the_tool_results(twin) -> None:
    project_id = uuid4()
    await _evidence(
        twin, project_id, {**BOOT, "result": {"verdict": "pass", "build_error_count": 4}}
    )
    facts = await read_design_facts(twin, project_id)
    assert facts["build_error_count"].value == 4
    assert facts["build_error_count"].origin == "evidence"


async def test_the_gate_fails_on_a_tool_measured_violation(twin) -> None:
    project_id = uuid4()
    await _evidence(
        twin, project_id, {**BOOT, "result": {"verdict": "fail", "boot_checks_failed": 2}}
    )
    await _seed(twin, project_id, _req("boot_checks_failed", "==", 0))

    report = await TwinConstraintChecker(twin).check(str(project_id))

    assert not report.passed
    assert any(v.startswith("boot_checks_failed:") for v in report.violations)
