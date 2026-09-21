"""Later phases' objectives instruct the brain to link a derived, lower-level
spec back to the requirement it implements via parent_refs (FORGE-49, epic
FORGE-35) -- the "flow from high-level to low-level requirements" completing
past the requirements phase itself.
"""

from __future__ import annotations

from orchestrator.design_flow.spec import DESIGN_V1, HARDWARE_V1, MECH_V1


def _phase(flow, phase_id: str):
    return next(p for p in flow.phases if p.id == phase_id)


def test_hardware_v1_architecture_phase_mentions_parent_refs() -> None:
    text = _phase(HARDWARE_V1, "architecture").objective
    assert "parent_refs" in text
    assert "record_constraint_set" in text


def test_hardware_v1_mechanical_design_phase_mentions_parent_refs() -> None:
    text = _phase(HARDWARE_V1, "design").objective
    assert "parent_refs" in text
    assert "record_constraint_set" in text


def test_mech_v1_design_phase_mentions_parent_refs() -> None:
    text = _phase(MECH_V1, "design").objective
    assert "parent_refs" in text
    assert "record_constraint_set" in text


def test_design_v1_design_phase_mentions_parent_refs() -> None:
    text = _phase(DESIGN_V1, "design").objective
    assert "parent_refs" in text
    assert "record_constraint_set" in text
