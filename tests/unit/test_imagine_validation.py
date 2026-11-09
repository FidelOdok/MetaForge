"""The pure half of Imagine validation (spec: imagine-validation-api-spec.md)."""

from __future__ import annotations

import copy
from typing import Any

from api_gateway.imagine.schemas import HandoverChecklist, HandoverItem
from api_gateway.imagine.validation import (
    MAX_LEVEL,
    Finding,
    Fix,
    Ref,
    build_report,
    compute_verdict,
    config_findings,
    handover_findings,
)
from tests.unit.imagine_fixtures import full_config

_STUDIO = Ref(kind="studio", id="x", revision=3)


def _f(
    status: str = "fail",
    resolver: str = "agent",
    severity: str = "blocking",
    gate: str = "handover",
    check_id: str = "x.y",
) -> Finding:
    return Finding(
        id=f"f_{check_id}_{status}_{resolver}",
        check_id=check_id,
        gate=gate,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        target=_STUDIO,
        message="m",
        resolver=resolver,  # type: ignore[arg-type]
    )


def _items(**status: str) -> HandoverChecklist:
    ids = [
        "ledger",
        "assumptions",
        "scenarios",
        "requirements",
        "parts",
        "manufacturing_context",
        "concept_assembly",
        "interfaces",
        "screening",
    ]
    return HandoverChecklist(
        ready=False,
        items=[HandoverItem(id=i, label=i, status=status.get(i, "done")) for i in ids],  # type: ignore[arg-type]
    )


def _collections() -> dict[str, Any]:
    return {"ledger": {}, "assumptions": {}, "scenarios": {}, "screening": {}}


# -- verdict precedence (spec 3.7) -----------------------------------------


def test_a_failing_config_check_is_invalid_before_anything_else() -> None:
    findings = [_f(gate="config_valid"), _f(resolver="agent"), _f(resolver="person")]
    assert compute_verdict(findings) == "invalid"


def test_the_verdict_names_who_acts_next_in_precedence_order() -> None:
    both = [_f(resolver="person"), _f(resolver="external"), _f(resolver="agent")]
    assert compute_verdict(both) == "needs_agent"
    assert compute_verdict([_f(resolver="person"), _f(resolver="external")]) == "blocked_external"
    assert compute_verdict([_f(resolver="person")]) == "needs_person"


def test_assumed_and_advisory_findings_do_not_block() -> None:
    findings = [
        _f(status="assumed", severity="advisory", resolver="person"),
        _f(status="fail", severity="advisory"),
        _f(status="pass"),
    ]
    assert compute_verdict(findings) == "ready_for_handover"


def test_a_check_that_could_not_run_blocks_when_the_check_is_blocking() -> None:
    assert compute_verdict([_f(status="unknown", resolver="external")]) == "blocked_external"
    assert compute_verdict([_f(status="unknown", severity="advisory")]) == "ready_for_handover"


# -- config_valid -----------------------------------------------------------


def test_each_engine_error_and_warning_is_one_finding() -> None:
    found = config_findings(["bad key"], ["odd value", "odd again"], 2)
    assert [f.severity for f in found] == ["blocking", "advisory", "advisory"]
    assert {f.gate for f in found} == {"config_valid"}
    assert [f.message for f in found] == ["bad key", "odd value", "odd again"]
    assert len({f.id for f in found}) == 3


# -- handover: the status mapping is exact (spec 5.1) ------------------------


def test_missing_assumed_and_done_map_to_fail_assumed_and_pass() -> None:
    found = handover_findings(
        full_config(),
        _collections(),
        _items(ledger="missing", assumptions="assumed", scenarios="done"),
        has_mesh=True,
        revision=3,
    )
    by_id = {f.id: f for f in found}
    assert by_id["f_ledger"].status == "fail" and by_id["f_ledger"].severity == "blocking"
    assert by_id["f_assumptions"].status == "assumed"
    assert by_id["f_assumptions"].severity == "advisory"
    assert by_id["f_scenarios"].status == "pass"


def test_who_clears_each_item_is_fixed_by_the_check() -> None:
    found = handover_findings(full_config(), _collections(), _items(), has_mesh=False, revision=1)
    resolver = {f.check_id: f.resolver for f in found}
    assert resolver["ledger.reviewed"] == "agent"
    assert resolver["assumptions.settled"] == "person"
    assert resolver["scenarios.confirmed"] == "person"
    assert resolver["concept.mesh"] == "external"


def test_the_parts_item_splits_per_part_per_gap() -> None:
    cfg = copy.deepcopy(full_config())
    parts = cfg["context"]["components"]["parts"]
    usbc = next(p for p in parts if p["id"] == "usbc-a")
    usbc.pop("datasheet")
    usbc["geometry"].pop("envelope")
    usbc["geometry"].pop("source")  # _part_gaps also accepts the geometry's source
    found = handover_findings(cfg, _collections(), _items(), has_mesh=True, revision=3)
    failing = {f.id: f for f in found if f.gate == "handover" and f.status == "fail"}
    assert "f_parts_usbc-a_source" in failing
    assert "f_parts_usbc-a_3d_envelope" in failing
    source = failing["f_parts_usbc-a_source"]
    assert source.check_id == "parts.source"
    assert source.target == Ref(kind="part", id="usbc-a", revision=3)
    assert source.resolver == "agent"


def test_a_fix_names_what_it_needs_and_never_carries_a_value() -> None:
    assert "value" not in Fix.model_fields
    cfg = copy.deepcopy(full_config())
    for p in cfg["context"]["components"]["parts"]:
        p.pop("interfaces", None)
    found = handover_findings(cfg, _collections(), _items(), has_mesh=True, revision=3)
    fixes = [f.fix for f in found if f.fix is not None]
    assert fixes and all(fx.needs for fx in fixes)


# -- the report -------------------------------------------------------------


def test_a_report_lists_assumed_findings_as_debt_and_stays_at_v1_without_screening() -> None:
    found = handover_findings(
        full_config(),
        _collections(),
        _items(assumptions="assumed", screening="missing"),
        has_mesh=True,
        revision=3,
    )
    report = build_report("p1", 3, found, ["handover"], computed_at="2026-11-09T00:00:00Z")
    assert [f.check_id for f in report.debt] == ["assumptions.settled"]
    assert report.level == "V1"
    assert report.verdict == "needs_person"
    assert report.gates[0].assumed == 1


def test_no_report_claims_above_v2() -> None:
    found = handover_findings(full_config(), _collections(), _items(), has_mesh=True, revision=3)
    report = build_report("p1", 3, found, ["handover"], computed_at="2026-11-09T00:00:00Z")
    assert report.level == MAX_LEVEL == "V2"
    assert report.verdict == "ready_for_handover"
    assert report.model_dump(mode="json")["level"] in {"V0", "V1", "V2"}


def test_a_failing_config_reports_v0() -> None:
    report = build_report(
        "p1",
        None,
        config_findings(["broken"], [], None),
        ["config_valid"],
        computed_at="2026-11-09T00:00:00Z",
    )
    assert report.level == "V0"
    assert report.verdict == "invalid"
    assert report.gates[0].result == "fail"


def test_the_same_input_gives_the_same_report() -> None:
    args = (full_config(), _collections(), _items(ledger="missing"))
    a = handover_findings(*args, has_mesh=True, revision=3)
    b = handover_findings(*args, has_mesh=True, revision=3)
    ra = build_report("p1", 3, a, ["handover"], computed_at="t")
    rb = build_report("p1", 3, b, ["handover"], computed_at="t")
    assert ra.model_dump_json() == rb.model_dump_json()
