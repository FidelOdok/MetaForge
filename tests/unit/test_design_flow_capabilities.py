"""Capability coverage and the gap register (FORGE-539)."""

from __future__ import annotations

from orchestrator.design_flow.capabilities import Coverage, GapSeverity, assess_capabilities
from orchestrator.design_flow.spec import Gate, Phase

PRODUCERS = {
    "simulation_result": ["freecad.generate_mesh", "calculix.run_fea", "twin.record_document"],
    "design_decision": ["twin.record_decision"],
}
ALL = {"freecad.generate_mesh", "calculix.run_fea", "twin.record_document", "twin.record_decision"}


def _sim(**kw: object) -> Phase:
    return Phase(
        id="simulation",
        title="s",
        objective="s",
        expected_artifacts=("simulation_result", "design_decision"),
        required_deliverables=("simulation_result",),
        gate=Gate(name="V&V sign-off"),
        **kw,  # type: ignore[arg-type]
    )


def test_everything_present_is_ready() -> None:
    report = assess_capabilities(
        [_sim()], producers=PRODUCERS, registered=ALL, reachable=ALL, served=ALL
    )
    assert report.status == "READY"
    assert report.nodes[0].coverage is Coverage.FULL
    assert report.gaps == () and report.limits == ()


def test_a_missing_solver_is_a_new_capability_and_blocks() -> None:
    # Spec test 1: the simulator is missing. Recognised, impact stated,
    # alternatives offered.
    registered = ALL - {"calculix.run_fea"}
    report = assess_capabilities([_sim()], producers=PRODUCERS, registered=registered)
    gap = report.gaps[0]
    assert report.nodes[0].coverage is Coverage.PARTIAL
    assert gap.missing_tools == ("calculix.run_fea",)
    assert gap.severity is GapSeverity.DEGRADES_CONFIDENCE
    assert gap.workarounds, "a gap names the ways to keep going"


def test_nothing_registered_blocks_the_step() -> None:
    report = assess_capabilities([_sim()], producers=PRODUCERS, registered={"twin.record_decision"})
    gap = report.gaps[0]
    assert gap.blocking and gap.severity is GapSeverity.REQUIRES_NEW_CAPABILITY
    assert report.status == "BLOCKED"


def test_a_down_adapter_is_a_user_action_and_blocks() -> None:
    reachable = ALL - {"calculix.run_fea"}
    report = assess_capabilities([_sim()], producers=PRODUCERS, registered=ALL, reachable=reachable)
    gap = report.gaps[0]
    assert gap.severity is GapSeverity.REQUIRES_USER_ACTION
    assert gap.unreachable_tools == ("calculix.run_fea",)
    assert gap.blocking
    assert "start the container" in gap.consequence


def test_an_unserved_tool_points_at_the_profile() -> None:
    served = ALL - {"twin.record_document"}
    report = assess_capabilities(
        [_sim()], producers=PRODUCERS, registered=ALL, reachable=ALL, served=served
    )
    gap = report.gaps[0]
    assert gap.unserved_tools == ("twin.record_document",)
    assert "profile" in gap.consequence
    assert not gap.blocking  # the user can reconnect; the toolchain exists


def test_an_expected_only_deliverable_never_blocks() -> None:
    report = assess_capabilities(
        [_sim()], producers=PRODUCERS, registered=ALL - {"twin.record_decision"}, reachable=None
    )
    gap = next(g for g in report.gaps if g.capability == "design_decision")
    assert not gap.blocking and gap.severity is GapSeverity.DEGRADES_CONFIDENCE


def test_a_deliverable_with_no_known_producer_is_unknown() -> None:
    phase = Phase(
        id="p",
        title="p",
        objective="p",
        required_deliverables=("pinmap",),
        expected_artifacts=("pinmap",),
    )
    report = assess_capabilities([phase], producers=PRODUCERS, registered=ALL)
    assert report.nodes[0].coverage is Coverage.UNKNOWN
    assert report.gaps == ()


def test_unchecked_inputs_are_stated_as_limits() -> None:
    report = assess_capabilities([_sim()], producers=PRODUCERS, registered=ALL)
    assert len(report.limits) == 2


def test_as_dict() -> None:
    import json

    report = assess_capabilities([_sim()], producers=PRODUCERS, registered=set())
    assert json.loads(json.dumps(report.as_dict()))["status"] == "BLOCKED"
