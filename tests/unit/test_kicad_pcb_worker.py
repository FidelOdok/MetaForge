"""The pcbnew worker's pure helpers (FORGE-691).

The worker runs under KiCad's own Python and imports ``pcbnew`` at the top,
so it is loaded here against a stand-in module; only functions that never
touch pcbnew are exercised. The routing itself is covered live in
``tests/integration/test_kicad_generation_live.py``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[2] / "tool_registry/tools/kicad/pcb_worker.py"

# What KiCad 9's MountingHole.pretty holds for plain holes, plus pad variants
# that must not be picked for an unplated hole.
LIBRARY = [
    "MountingHole_2.1mm",
    "MountingHole_2.2mm_M2",
    "MountingHole_2.2mm_M2_Pad",
    "MountingHole_2.5mm",
    "MountingHole_2.5mm_Pad",
    "MountingHole_2.7mm",
    "MountingHole_2.7mm_M2.5",
    "MountingHole_3.2mm_M3",
    "MountingHole_3.2mm_M3_DIN965",
    "MountingHole_3.2mm_M3_Pad_Via",
    "MountingHole_3mm",
]


@pytest.fixture(scope="module")
def worker() -> types.ModuleType:
    stub = types.ModuleType("pcbnew")
    stub.FromMM = lambda v: int(v * 1_000_000)  # type: ignore[attr-defined]
    saved = sys.modules.get("pcbnew")
    sys.modules["pcbnew"] = stub
    try:
        spec = importlib.util.spec_from_file_location("pcb_worker_under_test", WORKER)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("pcbnew", None)
        else:
            sys.modules["pcbnew"] = saved
    return mod


@pytest.mark.parametrize(
    ("drill", "expected"),
    [
        (3.2, "MountingHole_3.2mm_M3"),
        (2.5, "MountingHole_2.5mm"),
        (2.2, "MountingHole_2.2mm_M2"),
        (2.7, "MountingHole_2.7mm_M2.5"),
        (3.0, "MountingHole_3mm"),
        (2.4, None),
    ],
)
def test_mounting_hole_name_matches_the_drill(
    worker: types.ModuleType, drill: float, expected: str | None
) -> None:
    assert worker.mounting_hole_name(drill, LIBRARY) == expected


def test_mounting_hole_name_never_picks_a_pad_variant(worker: types.ModuleType) -> None:
    assert worker.mounting_hole_name(2.5, ["MountingHole_2.5mm_Pad"]) is None
    assert worker.mounting_hole_name(3.2, ["MountingHole_3.2mm_M3_Pad_Via"]) is None
