"""The accepted bracket workflow is a sound contract (FORGE-563).

Both suites rely on it: generation must be able to produce it, and every
lifecycle test starts from it. These checks pin down what "accepted" means,
so a lifecycle failure can never be a broken fixture in disguise.
"""

from __future__ import annotations

from orchestrator.design_flow.graph import build_graph
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.versions import VersionStatus
from tests.workflow.scenarios import bracket


def test_it_is_approved_startable_and_frozen(accepted_bracket: bracket.Accepted) -> None:
    v = accepted_bracket.version
    assert v.status is VersionStatus.APPROVED
    assert v.startable
    accepted_bracket.frozen.verify()  # the content hash matches the content


def test_the_frozen_content_round_trips(accepted_bracket: bracket.Accepted) -> None:
    rebuilt = accepted_bracket.definition
    assert [p.id for p in rebuilt.phases] == [
        p.id for p in accepted_bracket.version.definition.phases
    ]
    assert validate_flow(rebuilt).ok
    build_graph(rebuilt.phases)  # a valid dependency graph


def test_every_requirement_is_verified_by_required_evidence_at_a_gate(
    accepted_bracket: bracket.Accepted,
) -> None:
    gated_required = {
        d
        for p in accepted_bracket.definition.phases
        if p.gate is not None
        for d in p.required_deliverables
    }
    for req in bracket.REQUIREMENTS:
        assert req["verified_by"] in gated_required, req["id"]


def test_the_part_has_a_fixed_item_and_every_phase_an_outcome(
    accepted_bracket: bracket.Accepted,
) -> None:
    assert accepted_bracket.item_key("design", "cad_model") == "CAD-BRACKET"
    assert all(p.outcome for p in accepted_bracket.definition.phases)


def test_the_physical_case_is_fixed() -> None:
    # The numbers the expected behaviour rests on; changing one is a scenario change.
    assert bracket.LOAD_N == round(bracket.LOAD_KG * 9.81, 1)
    assert bracket.YIELD_MPA == 276.0 and bracket.MIN_SAFETY_FACTOR == 2.0
    assert bracket.context().loads_known
