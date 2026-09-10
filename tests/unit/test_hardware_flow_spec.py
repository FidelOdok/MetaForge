"""The full hardware/robotics lifecycle flow — structure + validity (MET-10)."""

from __future__ import annotations

from orchestrator.design_flow.spec import HARDWARE_V1, get_flow
from twin_core.models.enums import WorkProductType

VALID_TYPES = {t.value for t in WorkProductType}
# Producible today (have a creation tool), so they may be *required* at a gate.
# constraint_set: twin.record_constraint_set (MET-582).
PRODUCIBLE = {"design_decision", "cad_model", "constraint_set"}


def test_hardware_flow_registered_and_ordered() -> None:
    flow = get_flow("hardware_v1")
    assert flow is HARDWARE_V1
    assert [p.id for p in flow.phases] == [
        "requirements",
        "architecture",
        "design",
        "electronics",
        "firmware",
        "simulation",
        "manufacturing",
    ]


def test_every_phase_is_gated_and_enforced() -> None:
    for p in HARDWARE_V1.phases:
        assert p.gate is not None, f"{p.id} has no gate"
        assert p.gate.criteria, f"{p.id} gate has no review criteria"
        assert p.enforce_deliverables is True, f"{p.id} does not enforce deliverables"
        assert p.required_deliverables, f"{p.id} requires no deliverable"


def test_required_deliverables_are_producible_today() -> None:
    # A gate can only pass if its required deliverable has a creation tool — else
    # the flow would deadlock. The mechanical phase requires a cad_model; the
    # rest a design_decision.
    for p in HARDWARE_V1.phases:
        for d in p.required_deliverables:
            assert d in PRODUCIBLE, f"{p.id} requires non-producible '{d}'"
    design = next(p for p in HARDWARE_V1.phases if p.id == "design")
    assert "cad_model" in design.required_deliverables


def test_all_artifact_types_are_valid_work_products() -> None:
    # Guards against typos in expected/required artifact type strings.
    for p in HARDWARE_V1.phases:
        for t in (*p.expected_artifacts, *p.required_deliverables):
            assert t in VALID_TYPES, f"{p.id}: '{t}' is not a WorkProductType"


def test_covers_the_core_disciplines() -> None:
    ids = {p.id for p in HARDWARE_V1.phases}
    # mechanical(design), electronics, firmware, systems(architecture),
    # simulation, manufacturing, product-definition(requirements)
    assert {"architecture", "electronics", "firmware", "manufacturing"} <= ids
