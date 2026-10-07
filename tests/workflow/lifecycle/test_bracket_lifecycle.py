"""Workflow lifecycle, bracket: did we manage the workflow as things changed? (FORGE-565)

Every test starts from the same accepted bracket workflow (the contract
fixture), injects one event, and asserts the response. A bad initial plan
cannot obscure the behaviour under test, because there is no plan to get
wrong: the workflow is fixed.

Expected behaviour the code does not have yet is ``xfail(strict=True)`` with
its ticket, so the gap stays visible and the marker must come off when it
closes.
"""

from __future__ import annotations

import pytest

from mcp_core.profiles import DELIVERABLE_TOOLS
from orchestrator.design_flow.capabilities import assess_capabilities
from orchestrator.design_flow.failures import FailureClass, FailureResponse, classify_failure
from orchestrator.design_flow.graph import build_graph, rework_candidates
from orchestrator.design_flow.lifecycle import (
    CompletionClass,
    Eligibility,
    ExecutionStatus,
    ObjectiveStatus,
    RequirementStatus,
    Validity,
)
from orchestrator.design_flow.patch import StalePatchError, plan_patch
from orchestrator.design_flow.retry import max_phase_retries
from orchestrator.design_flow.rework import max_rework_cycles, rework_target_error
from tests.workflow.harness import Run
from tests.workflow.scenarios import bracket

ALL_DONE = ("intent", "needs", "requirements", "feasibility", "design", "simulation")


def _verified_run(accepted: bracket.Accepted) -> Run:
    run = Run(accepted).succeed(*ALL_DONE)
    for req in bracket.REQUIREMENTS:
        run.evidence(req["id"], "pass")
    return run.finish()


def test_the_happy_path_is_verified(accepted_bracket: bracket.Accepted) -> None:
    """Baseline: with every result current and passing, the run is verified."""
    view = _verified_run(accepted_bracket).view()
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED


# --- 1. The analysis ran, but the design fails its objective -------------------


def test_a_failed_safety_factor_is_an_objective_failure_not_an_execution_failure(
    accepted_bracket: bracket.Accepted,
) -> None:
    run = Run(accepted_bracket).succeed_through("design").gate_not_ready("simulation")
    run.evidence("REQ-FOS", "fail")
    node = run.view().node("simulation")
    assert node.objective_status is ObjectiveStatus.UNSATISFIED
    assert node.execution_status not in (
        ExecutionStatus.FAILED_RETRYABLE,
        ExecutionStatus.FAILED_NONRETRYABLE,
    )
    assert run.view().completion.classification is CompletionClass.BLOCKED


def test_a_design_failure_is_repaired_by_replanning_the_design_not_retrying(
    accepted_bracket: bracket.Accepted,
) -> None:
    verdict = classify_failure("safety factor 1.6 is below the required 2.0")
    assert verdict.failure_class is FailureClass.DESIGN
    assert verdict.response is FailureResponse.REPLAN
    assert not verdict.retryable


def test_the_repair_is_local_to_the_design(accepted_bracket: bracket.Accepted) -> None:
    graph = build_graph(accepted_bracket.definition.phases)
    candidates = rework_candidates(graph, "simulation")
    assert rework_target_error(candidates, "simulation", "design") is None
    # Rework keeps everything before the target: requirements are not redone.
    run = Run(accepted_bracket).succeed_through("design").gate_not_ready("simulation")
    run.rework_to("design")
    view = run.view()
    for kept in ("intent", "needs", "requirements", "feasibility"):
        assert view.node(kept).execution_status is ExecutionStatus.SUCCEEDED
        assert view.node(kept).validity is Validity.VALID
    # The run is back on the design; the analysis waits for the new geometry.
    assert view.node("design").execution_status is ExecutionStatus.RUNNING
    assert view.node("simulation").execution_status is ExecutionStatus.PENDING
    assert view.node("simulation").eligibility is Eligibility.WAITING_FOR_DEPENDENCY


# --- 2. The load changes from 10 kg to 15 kg -----------------------------------


def test_a_load_change_reruns_affected_work_and_keeps_the_rest(
    accepted_bracket: bracket.Accepted,
) -> None:
    plan = plan_patch(
        accepted_bracket.definition,
        current_hash=accepted_bracket.content_hash,
        expected_hash=accepted_bracket.content_hash,
        operations=[],
        invalidate=["requirements"],
        completed=list(ALL_DONE),
    )
    assert plan.valid
    assert plan.rerun == ("requirements", "feasibility", "design", "simulation")
    assert plan.preserved == ("intent", "needs")


def test_a_patch_against_a_superseded_version_is_refused(
    accepted_bracket: bracket.Accepted,
) -> None:
    with pytest.raises(StalePatchError):
        plan_patch(
            accepted_bracket.definition,
            current_hash=accepted_bracket.content_hash,
            expected_hash="0" * 64,
            operations=[],
            invalidate=["requirements"],
        )


def test_the_patched_flow_is_a_new_version_and_the_old_one_is_kept(
    accepted_bracket: bracket.Accepted,
) -> None:
    plan = plan_patch(
        accepted_bracket.definition,
        current_hash=accepted_bracket.content_hash,
        expected_hash=accepted_bracket.content_hash,
        operations=[
            {
                "op": "set_outcome",
                "phase": "simulation",
                "value": "factor of safety at least 2 under 147.2 N (15 kg)",
                "rationale": "payload raised from 10 kg to 15 kg",
            }
        ],
        invalidate=["requirements"],
        completed=list(ALL_DONE),
    )
    store = accepted_bracket.store
    patched = store.save(
        plan.definition,
        base_template_id=accepted_bracket.version.base_template_id,
        base_version=accepted_bracket.version.base_version,
        changes=plan.diff("payload raised from 10 kg to 15 kg"),
        origin="patch",
    )
    assert patched.id != accepted_bracket.version.id
    assert patched.frozen.content_hash != accepted_bracket.content_hash
    assert store.get(accepted_bracket.version.id).frozen.content_hash == (
        accepted_bracket.content_hash
    )


# --- 3. A provider fails temporarily --------------------------------------------


def test_a_transient_provider_failure_is_retried_within_a_bound() -> None:
    verdict = classify_failure("calculix adapter: connection reset by peer (503)")
    assert verdict.failure_class is FailureClass.TRANSIENT
    assert verdict.response is FailureResponse.RETRY and verdict.retryable
    assert 0 < max_phase_retries() <= 5


# --- 4/5. A required capability disappears, then comes back ---------------------


def _report(accepted: bracket.Accepted, registered: set[str]):
    return assess_capabilities(
        accepted.definition.phases, producers=DELIVERABLE_TOOLS, registered=registered
    )


def _without_solver() -> set[str]:
    return bracket.all_tools() - {
        t for t in DELIVERABLE_TOOLS["simulation_result"] if t.startswith(("calculix.", "freecad."))
    }


def test_a_lost_solver_is_reported_with_alternatives(accepted_bracket: bracket.Accepted) -> None:
    gaps = [
        g for g in _report(accepted_bracket, _without_solver()).gaps if g.phase_id == "simulation"
    ]
    assert gaps and "calculix.run_fea" in gaps[0].missing_tools
    assert gaps[0].workarounds


def test_a_lost_solver_blocks_the_analysis(accepted_bracket: bracket.Accepted) -> None:
    report = _report(accepted_bracket, _without_solver())
    assert "simulation" in {g.phase_id for g in report.blocking_gaps}


def test_a_blocking_gap_makes_the_affected_node_blocked(
    accepted_bracket: bracket.Accepted,
) -> None:
    run = Run(accepted_bracket).succeed_through("design")
    run.gaps = [{"phase": "simulation", "capability": "simulation_result", "blocking": True}]
    assert run.view().node("simulation").eligibility is Eligibility.BLOCKED


def test_a_returning_capability_releases_the_work(accepted_bracket: bracket.Accepted) -> None:
    assert not _report(accepted_bracket, bracket.all_tools()).gaps
    run = Run(accepted_bracket).succeed_through("design")
    assert run.view().node("simulation").eligibility is Eligibility.ELIGIBLE


# --- 6. Old evidence arrives after a revision --------------------------------------


def test_evidence_from_before_a_revision_is_kept_but_not_current(
    accepted_bracket: bracket.Accepted,
) -> None:
    run = _verified_run(accepted_bracket)
    run.revise(accepted_bracket.item_key("design", "cad_model"))
    run.evidence("REQ-FOS", "stale")
    view = run.view()
    assert view.node("design").validity is Validity.STALE
    assert view.node("simulation").validity is Validity.POTENTIALLY_INVALID
    # History preserved: the row is still there, as stale rather than dropped.
    fos = next(r for r in view.requirements if r["id"] == "REQ-FOS")
    assert fos["status"] == RequirementStatus.STALE.value
    assert not view.completion.verified


# --- 7. Every task finished, but a requirement fails ---------------------------------


def test_finishing_every_task_with_a_failing_requirement_is_not_verified(
    accepted_bracket: bracket.Accepted,
) -> None:
    run = _verified_run(accepted_bracket).evidence("REQ-FOS", "fail")
    completion = run.view().completion
    assert completion.classification is CompletionClass.PARTIALLY_COMPLETED
    assert [u["id"] for u in completion.unmet_requirements] == ["REQ-FOS"]


def test_no_evidence_is_not_a_pass(accepted_bracket: bracket.Accepted) -> None:
    run = Run(accepted_bracket).succeed(*ALL_DONE).finish()  # every requirement no_data
    assert run.view().completion.classification is CompletionClass.PARTIALLY_COMPLETED


# --- 8. Iterations stop improving ----------------------------------------------------


def test_repair_iterations_are_bounded() -> None:
    assert 0 < max_rework_cycles() <= 5
    assert 0 < max_phase_retries() <= 5
