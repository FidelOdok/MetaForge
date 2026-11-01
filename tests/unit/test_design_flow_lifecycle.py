"""The lifecycle view and the completion verdict (FORGE-539)."""

from __future__ import annotations

from orchestrator.design_flow.lifecycle import (
    CompletionClass,
    Eligibility,
    ExecutionStatus,
    ObjectiveStatus,
    Validity,
    lifecycle_view,
    normalize_requirement_status,
)
from orchestrator.design_flow.spec import DeliverableSlot, Gate, Phase


def _phase(
    pid: str,
    deps: tuple[str, ...] | None = None,
    *,
    key: str = "",
    gated: bool = True,
    condition: str | None = None,
) -> Phase:
    return Phase(
        id=pid,
        title=pid,
        objective=pid,
        gate=Gate(name=f"{pid} gate") if gated else None,
        depends_on=deps,
        condition=condition,
        slots=(DeliverableSlot(item_type="cad_model", name=pid, item_key=key),) if key else (),
    )


PHASES = [
    _phase("req"),
    _phase("mech", ("req",), key="CAD-FRAME"),
    _phase("elec", ("req",)),
    _phase("verify", ("mech", "elec")),
]


def _done(*ids: str) -> list[dict[str, str]]:
    return [{"phase": i, "status": "completed"} for i in ids]


ALL = _done("req", "mech", "elec", "verify")
PASSING = [{"id": "REQ-1", "status": "pass"}, {"id": "REQ-2", "status": "pass"}]


class TestCompletionVerdict:
    def test_everything_passes_is_verified(self) -> None:
        view = lifecycle_view(
            PHASES, {"status": "completed", "completed": ALL}, requirements=PASSING
        )
        assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED
        assert view.completion.verified

    def test_all_phases_done_but_a_requirement_fails_is_not_completed(self) -> None:
        # The spec's test 7: every task ended, the intent is not satisfied.
        reqs = [{"id": "REQ-1", "status": "pass"}, {"id": "REQ-2", "status": "fail"}]
        view = lifecycle_view(PHASES, {"status": "completed", "completed": ALL}, requirements=reqs)
        assert view.completion.classification is CompletionClass.PARTIALLY_COMPLETED
        assert not view.completion.verified
        assert view.completion.unmet_requirements == ({"id": "REQ-2", "status": "FAIL"},)

    def test_no_data_is_never_a_pass(self) -> None:
        reqs = [{"id": "REQ-1", "status": "no_data"}]
        view = lifecycle_view(PHASES, {"status": "completed", "completed": ALL}, requirements=reqs)
        assert view.completion.classification is CompletionClass.PARTIALLY_COMPLETED
        assert "NOT_EVALUATED" in view.completion.reasons[0]

    def test_an_optional_requirement_does_not_block(self) -> None:
        reqs = [*PASSING, {"id": "NICE-1", "status": "fail", "mandatory": False}]
        view = lifecycle_view(PHASES, {"status": "completed", "completed": ALL}, requirements=reqs)
        assert view.completion.verified

    def test_no_requirements_at_all_is_a_warning_not_a_verification(self) -> None:
        view = lifecycle_view(PHASES, {"status": "completed", "completed": ALL})
        assert view.completion.classification is CompletionClass.COMPLETED_WITH_WARNINGS
        assert "nothing was verified" in view.completion.warnings[0]

    def test_a_waiver_passes_with_a_warning(self) -> None:
        reqs = [{"id": "REQ-1", "status": "waived"}]
        view = lifecycle_view(PHASES, {"status": "completed", "completed": ALL}, requirements=reqs)
        assert view.completion.classification is CompletionClass.COMPLETED_WITH_WARNINGS
        assert "waiver" in view.completion.warnings[0]

    def test_a_blocking_gap_prevents_verification(self) -> None:
        gaps = [{"capability": "spice simulation", "blocking": True}]
        view = lifecycle_view(
            PHASES, {"status": "completed", "completed": ALL}, requirements=PASSING, gaps=gaps
        )
        assert view.completion.classification is CompletionClass.PARTIALLY_COMPLETED

    def test_a_failed_run_is_failed_and_a_rejected_one_cancelled(self) -> None:
        failed = lifecycle_view(PHASES, {"status": "failed", "error": "boom", "completed": []})
        assert failed.completion.classification is CompletionClass.FAILED
        rejected = lifecycle_view(PHASES, {"status": "rejected", "completed": _done("req")})
        assert rejected.completion.classification is CompletionClass.CANCELLED

    def test_a_running_run_is_in_progress(self) -> None:
        view = lifecycle_view(PHASES, {"status": "running", "completed": _done("req")})
        assert view.completion.classification is CompletionClass.IN_PROGRESS


class TestInvalidation:
    def test_a_stale_item_marks_its_phase_and_everything_downstream(self) -> None:
        view = lifecycle_view(
            PHASES,
            {"status": "completed", "completed": ALL},
            stale_item_keys=["CAD-FRAME"],
            requirements=PASSING,
        )
        assert view.node("mech").validity is Validity.STALE
        assert view.node("verify").validity is Validity.POTENTIALLY_INVALID
        # elec does not depend on mech: untouched.
        assert view.node("elec").validity is Validity.VALID
        assert view.node("req").validity is Validity.VALID
        assert view.completion.classification is CompletionClass.PARTIALLY_COMPLETED
        assert "superseded" in " ".join(view.completion.reasons)

    def test_not_yet_run_is_unknown_not_valid(self) -> None:
        view = lifecycle_view(PHASES, {"status": "running", "completed": _done("req")})
        assert view.node("verify").validity is Validity.UNKNOWN


class TestNodeState:
    def test_execution_and_objective_are_separate(self) -> None:
        state = {
            "status": "awaiting_approval",
            "completed": _done("req", "mech", "elec", "verify"),
            "current_phase": "verify",
            "awaiting_gate": "verify gate",
            "gate_ready": False,
        }
        view = lifecycle_view(PHASES, state)
        verify = view.node("verify")
        # It ran; its gate says the objective is not met.
        assert verify.execution_status is ExecutionStatus.WAITING
        assert verify.objective_status is ObjectiveStatus.UNSATISFIED
        assert verify.eligibility is Eligibility.WAITING_FOR_APPROVAL
        assert view.completion.classification is CompletionClass.BLOCKED

    def test_eligibility_follows_the_graph(self) -> None:
        state = {"status": "running", "completed": _done("req", "mech"), "running": ["elec"]}
        view = lifecycle_view(PHASES, state)
        assert view.node("elec").execution_status is ExecutionStatus.RUNNING
        assert view.node("verify").eligibility is Eligibility.WAITING_FOR_DEPENDENCY
        state = {"status": "running", "completed": _done("req")}
        view = lifecycle_view(PHASES, state)
        assert view.node("mech").eligibility is Eligibility.ELIGIBLE
        assert view.node("elec").eligibility is Eligibility.ELIGIBLE

    def test_a_skipped_phase_is_skipped_not_done(self) -> None:
        phases = [_phase("req"), _phase("route", ("req",), condition="route == undecided")]
        view = lifecycle_view(
            phases,
            {"status": "completed", "completed": _done("req"), "skipped": ["route"]},
            requirements=PASSING,
        )
        assert view.node("route").execution_status is ExecutionStatus.SKIPPED
        assert view.completion.classification is CompletionClass.COMPLETED_WITH_WARNINGS
        assert any("skipped" in w for w in view.completion.warnings)

    def test_an_ungrounded_phase_did_not_meet_its_objective(self) -> None:
        state = {
            "status": "completed",
            "completed": [
                *_done("req", "mech", "elec"),
                {"phase": "verify", "status": "ungrounded"},
            ],
        }
        view = lifecycle_view(PHASES, state, requirements=PASSING)
        assert view.node("verify").objective_status is ObjectiveStatus.UNSATISFIED
        assert view.completion.classification is CompletionClass.PARTIALLY_COMPLETED

    def test_a_terminal_run_blocks_pending_phases(self) -> None:
        view = lifecycle_view(PHASES, {"status": "rejected", "completed": _done("req")})
        assert view.node("mech").eligibility is Eligibility.BLOCKED

    def test_as_dict_is_plain_data(self) -> None:
        import json

        view = lifecycle_view(
            PHASES, {"status": "completed", "completed": ALL}, requirements=PASSING
        )
        assert json.loads(json.dumps(view.as_dict()))["completion"]["verified"] is True


def test_matrix_statuses_map_onto_the_contract() -> None:
    assert normalize_requirement_status("uncertain").value == "INCONCLUSIVE"
    assert normalize_requirement_status("no_data").value == "NOT_EVALUATED"
    assert normalize_requirement_status("stale").value == "STALE"
    assert normalize_requirement_status("something new").value == "NOT_EVALUATED"


class TestBlockingGapsBlockTheirNodeForge572:
    def test_a_blocking_gap_blocks_the_pending_node_it_names(self) -> None:
        view = lifecycle_view(
            PHASES,
            {"status": "running", "completed": _done("req", "elec")},
            gaps=[{"phase": "mech", "capability": "cad_model", "blocking": True}],
        )
        mech = view.node("mech")
        assert mech.eligibility is Eligibility.BLOCKED
        assert any("cad_model" in r for r in mech.reasons)

    def test_a_non_blocking_gap_leaves_the_node_eligible(self) -> None:
        view = lifecycle_view(
            PHASES,
            {"status": "running", "completed": _done("req", "elec")},
            gaps=[{"phase": "mech", "capability": "cad_model", "blocking": False}],
        )
        assert view.node("mech").eligibility is Eligibility.ELIGIBLE
