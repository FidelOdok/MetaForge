"""Design for assembly: placed bodies, the fit checks and their findings.

* :mod:`twin_core.dfa.geometry`: placements (``x, y, z, rx, ry, rz`` as
  ``cadquery.create_assembly`` takes them), joint rotations and a simple
  solid engine for bodies given as boxes.
* :mod:`twin_core.dfa.checks`: interference, clearance, hole patterns,
  outlines, edge connectors, stacks, joint motion and seated parts, each
  finding with a severity and the part revisions involved.

Pure apart from the geometry engine it is handed. The gateway builds and
checks assemblies (``api_gateway.twin.assembly_fit``); the release checklist
reads the recorded checks (``twin_core.dfm.release_fit``).
"""

from __future__ import annotations

from twin_core.dfa.checks import (
    AssemblyReport,
    AssemblySpec,
    AssemblySpecError,
    CheckResult,
    Finding,
    parse_spec,
    run_checks,
)
from twin_core.dfa.geometry import (
    Body,
    Box,
    EngineResult,
    GeometryEngine,
    Overlap,
    Pose,
    SimpleSolidEngine,
    location_matrix,
)

__all__ = [
    "AssemblyReport",
    "AssemblySpec",
    "AssemblySpecError",
    "Body",
    "Box",
    "CheckResult",
    "EngineResult",
    "Finding",
    "GeometryEngine",
    "Overlap",
    "Pose",
    "SimpleSolidEngine",
    "location_matrix",
    "parse_spec",
    "run_checks",
]
