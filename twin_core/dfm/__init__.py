"""Design for manufacture: rule profiles and cheap heuristics (C3D slice 4).

* :mod:`twin_core.dfm.profiles`: versioned, process-specific rule profiles,
  each rule with its kind and source (DFM-009, DFM-014).
* :mod:`twin_core.dfm.heuristics`: evaluate a component's best available
  geometry against a profile; anchored findings, explicit gaps and a status
  that never claims a pass (DFM-006, DFM-008, DFM-010, DFM-013).
* :mod:`twin_core.dfm.overhang`: the FDM overhang check on a face table.

Pure: no I/O. The gateway gathers geometry and stores finding sets
(``api_gateway.twin.dfm_findings``).
"""

from __future__ import annotations

from twin_core.dfm.heuristics import (
    Anchor,
    ComponentGeometry,
    DfmFinding,
    DfmFindingSet,
    FaceSample,
    MachineCapability,
    MachineContext,
    Measure,
    NotAssessed,
    ProcessContext,
    VariantGuidance,
    alternative_profiles,
    evaluate,
    finding_note,
    finding_set_status,
    material_family,
    product_status,
    variant_guidance,
)
from twin_core.dfm.profiles import PROFILES, DfmProfile, DfmRule, RuleKind, profile_for_process

__all__ = [
    "PROFILES",
    "Anchor",
    "ComponentGeometry",
    "DfmFinding",
    "DfmFindingSet",
    "DfmProfile",
    "DfmRule",
    "FaceSample",
    "MachineCapability",
    "MachineContext",
    "Measure",
    "NotAssessed",
    "ProcessContext",
    "RuleKind",
    "VariantGuidance",
    "alternative_profiles",
    "evaluate",
    "finding_note",
    "finding_set_status",
    "material_family",
    "product_status",
    "profile_for_process",
    "variant_guidance",
]
