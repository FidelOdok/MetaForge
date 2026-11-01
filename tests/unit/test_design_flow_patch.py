"""Planning a patch to a running flow (FORGE-539)."""

from __future__ import annotations

import pytest

from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.generator import TailoringError, apply_operations
from orchestrator.design_flow.patch import StalePatchError, plan_patch
from orchestrator.design_flow.spec import get_flow


def _graph_hardware():
    """hardware_v1 with electronics and firmware in parallel to mechanical design."""
    from orchestrator.design_flow.generator import Operation, OperationKind

    ops = [
        Operation(OperationKind.SET_DEPENDENCIES, "electronics", "r", ["concept_selection"]),
        Operation(OperationKind.SET_DEPENDENCIES, "firmware", "r", ["electronics"]),
        Operation(
            OperationKind.SET_DEPENDENCIES, "simulation", "r", ["design", "electronics", "firmware"]
        ),
    ]
    return apply_operations(get_flow("hardware_v1"), ops)[0]


FLOW = _graph_hardware()
HASH = freeze_flow(FLOW).content_hash
DONE = [
    "intent",
    "needs",
    "requirements",
    "feasibility",
    "architecture",
    "concept_selection",
    "design",
    "electronics",
]


def test_invalidating_one_branch_reruns_only_it_and_what_depends_on_it() -> None:
    # The spec's test 3: the payload changed, so mechanical design is stale.
    # Electronics is untouched and keeps its result.
    plan = plan_patch(
        FLOW,
        current_hash=HASH,
        expected_hash=HASH,
        operations=[],
        invalidate=["design"],
        completed=DONE,
    )
    assert plan.valid
    assert plan.rerun == ("design", "simulation", "manufacturing")
    assert "electronics" in plan.preserved and "requirements" in plan.preserved
    assert "design" not in plan.preserved


def test_a_changed_phase_is_rerun_without_being_named() -> None:
    ops = [
        {
            "op": "add_deliverable",
            "phase": "electronics",
            "value": "bom",
            "rationale": "the user now wants a costed BOM",
        }
    ]
    plan = plan_patch(FLOW, current_hash=HASH, expected_hash=HASH, operations=ops, completed=DONE)
    assert "electronics" in plan.rerun and "firmware" in plan.rerun
    assert "design" in plan.preserved
    assert any("re-run" in line for line in plan.diff("scope change"))


def test_a_stale_patch_is_refused() -> None:
    with pytest.raises(StalePatchError, match="reload"):
        plan_patch(FLOW, current_hash=HASH, expected_hash="0" * 64, operations=[])


def test_unknown_invalidation_is_refused() -> None:
    with pytest.raises(StalePatchError, match="unknown phase"):
        plan_patch(FLOW, current_hash=HASH, expected_hash=HASH, operations=[], invalidate=["ghost"])


def test_bad_operations_are_refused_strictly() -> None:
    with pytest.raises(TailoringError):
        plan_patch(
            FLOW,
            current_hash=HASH,
            expected_hash=HASH,
            operations=[{"op": "remove_gate", "phase": "design", "rationale": "x"}],
        )


def test_an_empty_patch_reruns_nothing_and_says_so() -> None:
    plan = plan_patch(FLOW, current_hash=HASH, expected_hash=HASH, operations=[], completed=DONE)
    assert plan.rerun == ()
    assert plan.preserved == tuple(DONE)
    assert "no phase is re-run" in plan.notes[0]


def test_an_invalid_result_is_reported_not_raised() -> None:
    ops = [{"op": "set_dependencies", "phase": "needs", "value": ["design"], "rationale": "x"}]
    plan = plan_patch(FLOW, current_hash=HASH, expected_hash=HASH, operations=ops, completed=DONE)
    assert not plan.valid
    assert plan.as_dict()["violations"]
