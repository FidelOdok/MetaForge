"""What the gateway keeps of Imagine validation: contract shapes, the interim config
gate and the unavailable answer. The rules, verdicts and levels are the verification
service's, so nothing here judges a studio.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from pydantic import ValidationError

from api_gateway.imagine import validation
from api_gateway.imagine.validation import (
    ValidatePost,
    ValidationReport,
    config_findings,
    unavailable_report,
)

# -- the interim config_valid gate ----------------------------------------------


def test_a_clean_config_says_so_because_the_service_never_assumes_it() -> None:
    found = config_findings([], [], 3)
    assert [(f.status, f.severity, f.gate) for f in found] == [("pass", "blocking", "config_valid")]
    assert found[0].target.revision == 3


def test_errors_are_blocking_failures_and_there_is_no_pass() -> None:
    found = config_findings(["bad key", "bad value"], ["odd"], None)
    assert [(f.status, f.severity) for f in found] == [
        ("fail", "blocking"),
        ("fail", "blocking"),
        ("fail", "advisory"),
    ]
    assert not any(f.status == "pass" for f in found)


def test_warnings_alongside_a_clean_config_are_advisory_and_follow_the_pass() -> None:
    found = config_findings([], ["odd value"], 1)
    assert [f.status for f in found] == ["pass", "fail"]
    assert found[1].severity == "advisory"


def test_every_config_finding_is_the_agents_to_fix_and_ids_are_unique() -> None:
    found = config_findings(["a", "b"], ["c", "d"], 1)
    assert {f.resolver for f in found} == {"agent"}
    assert {f.check_id for f in found} == {"config.formgraph"}
    assert len({f.id for f in found}) == len(found)


def test_a_long_engine_message_is_clipped_to_the_contracts_bound() -> None:
    assert len(config_findings(["x" * 900], [], 1)[0].message) == 500


def test_the_interim_gate_is_labelled_as_such() -> None:
    assert "interim" in config_findings([], [], 1)[0].rule_version


# -- the unavailable answer ---------------------------------------------------------


def test_unavailable_is_blocked_external_with_exactly_one_external_finding() -> None:
    report = unavailable_report("p1", 4, "could not reach the verification service")
    assert report.verdict == "blocked_external" and report.level == "V0"
    assert len(report.findings) == 1
    f = report.findings[0]
    assert (f.check_id, f.resolver, f.severity, f.status) == (
        "verification.service",
        "external",
        "blocking",
        "not_assessed",
    )
    assert f.target.revision == 4 and report.revision_number == 4
    assert report.gates == [] and report.debt == []


def test_unavailable_claims_nothing_above_v0_and_judges_nothing() -> None:
    report = unavailable_report("p1", None, "down")
    assert report.level == "V0" and report.revision_id == ""
    assert {f.status for f in report.findings} == {"not_assessed"}


# -- the report is rendered, not edited ---------------------------------------------


def _wire(**over: object) -> dict[str, object]:
    return {"level": "V2", "verdict": "ready_for_handover", **over}


def test_a_field_the_service_adds_survives_the_round_trip() -> None:
    report = ValidationReport.model_validate(_wire(future={"a": 1}))
    assert report.model_dump(mode="json")["future"] == {"a": 1}


def test_a_verdict_or_level_the_gateway_does_not_know_is_refused_not_trusted() -> None:
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(_wire(verdict="all_good_trust_me"))
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(_wire(level="V5"))


def test_a_finding_status_outside_the_contract_is_refused() -> None:
    bad = {
        "id": "f",
        "check_id": "c",
        "rule_version": "v",
        "gate": "handover",
        "severity": "blocking",
        "status": "verified",
        "target": {"kind": "studio", "id": "x"},
        "message": "m",
        "resolver": "agent",
    }
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(_wire(findings=[bad]))


def test_a_fix_has_no_value_field_to_invent() -> None:
    assert set(validation.ValidationFix.model_fields) == {"op", "pointer", "needs"}


# -- the request ---------------------------------------------------------------------


def test_validate_post_refuses_unknown_fields_and_unknown_gates() -> None:
    with pytest.raises(ValidationError):
        ValidatePost.model_validate({"surprise": 1})
    with pytest.raises(ValidationError):
        ValidatePost.model_validate({"gates": ["handover", "bogus"]})
    assert ValidatePost.model_validate({"gates": ["define_exit"]}).gates == ["define_exit"]


# -- the seam: no rule has crept back in --------------------------------------------


def test_this_module_decides_nothing() -> None:
    """A threshold or predicate lives in the service. If one appears here, one of them drifts."""
    tree = ast.parse(inspect.getsource(validation))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert defined == {"config_findings", "unavailable_report"}
