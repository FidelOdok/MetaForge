"""hardware_v1 design, electronics and firmware: the tool-using brain first.

Live evidence (run_08d61a4b18cd4ee5, 2026-11-15, a USB-C night light): the
hardware_v1 design phase committed a plain 80 x 30 mm solid cylinder, because
the phase ran the single-primitive handler with no CAD tools; the electronics
handler only records a BOM and a power budget as a decision, and the firmware
handler only a pin map and a scaffold. No phase ever called KiCad or the
firmware build, so no hardware_v1 run could produce a schematic, a board or a
built firmware however good the model was.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.runs.mech_handlers import NativeMechanicalDesignHandler
from api_gateway.runs.native_first import (
    ELECTRONICS_NOTE,
    FIRMWARE_NOTE,
    NativeFirstPhaseHandler,
)
from mcp_core.profiles import tools_for_phase
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import get_flow

_CTX = FlowContext(goal="a night light", project_id="p1")


def _phase(phase_id: str) -> Any:
    return next(p for p in get_flow("hardware_v1").phases if p.id == phase_id)


class _Brain:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.goals: list[str] = []
        self._fail = fail

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> PhaseOutcome:
        self.goals.append(goal)
        if self._fail is not None:
            raise self._fail
        return PhaseOutcome(summary=f"{phase.id} by the brain", artifacts=["x"], status="completed")


def _probe(present: set[str]) -> Any:
    async def probe(project_id: str | None, since_ts: float) -> set[str]:
        return set(present)

    return probe


# --- the handler -------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_outcome_kept_when_every_required_type_was_recorded() -> None:
    native, fallback = _Brain(), _Brain()
    handler = NativeFirstPhaseHandler(
        native,
        fallback,
        _probe({"schematic", "pcb_layout", "design_decision"}),
        required_types=("schematic", "pcb_layout"),
        note=ELECTRONICS_NOTE,
        label="electronics",
    )
    out = await handler.run_phase(goal="a night light", phase=_phase("electronics"), context=_CTX)
    assert out.summary == "electronics by the brain"
    assert fallback.goals == []
    assert ELECTRONICS_NOTE in native.goals[0]


@pytest.mark.asyncio
async def test_fallback_runs_and_says_so_when_a_required_type_is_missing() -> None:
    # negative control: a brain that only wrote a decision is not a board
    native, fallback = _Brain(), _Brain()
    handler = NativeFirstPhaseHandler(
        native,
        fallback,
        _probe({"schematic", "design_decision"}),
        required_types=("schematic", "pcb_layout"),
        note=ELECTRONICS_NOTE,
        label="electronics",
    )
    out = await handler.run_phase(goal="a night light", phase=_phase("electronics"), context=_CTX)
    assert fallback.goals == ["a night light"]
    assert out.summary.startswith("FALLBACK:")
    assert "pcb_layout" in out.summary


@pytest.mark.asyncio
async def test_fallback_runs_when_the_brain_raises() -> None:
    native, fallback = _Brain(fail=RuntimeError("model timed out")), _Brain()
    handler = NativeFirstPhaseHandler(
        native,
        fallback,
        _probe(set()),
        required_types=("firmware_source",),
        note=FIRMWARE_NOTE,
        label="firmware",
    )
    out = await handler.run_phase(goal="a night light", phase=_phase("firmware"), context=_CTX)
    assert "model timed out" in out.summary and out.summary.startswith("FALLBACK:")


def test_notes_name_the_tools_that_make_the_deliverables() -> None:
    for tool in (
        "kicad.generate_schematic",
        "kicad.generate_pcb",
        "kicad.run_drc",
        "twin.record_design_files",
    ):
        assert tool in ELECTRONICS_NOTE
    for tool in ("firmware.build", "firmware.static_check", "firmware.run_emulated"):
        assert tool in FIRMWARE_NOTE


# --- the phase tool sets -----------------------------------------------------


def _phase_tools(phase_id: str) -> frozenset[str]:
    phase = _phase(phase_id)
    deliverables = tuple(dict.fromkeys([*phase.required_deliverables, *phase.expected_artifacts]))
    return tools_for_phase(phase.disciplines, deliverables)


def test_electronics_phase_can_author_a_board() -> None:
    tools = _phase_tools("electronics")
    for tool in (
        "kicad.generate_schematic",
        "kicad.generate_pcb",
        "kicad.autoroute_pcb",
        "kicad.run_erc",
        "kicad.run_drc",
        "twin.record_design_files",
    ):
        assert tool in tools, tool


def test_firmware_phase_can_build_check_and_emulate() -> None:
    tools = _phase_tools("firmware")
    for tool in (
        "firmware.build",
        "firmware.static_check",
        "firmware.run_emulated",
        "twin.record_design_files",
    ):
        assert tool in tools, tool


def test_design_phase_can_author_cad() -> None:
    tools = _phase_tools("design")
    for tool in (
        "freecad.open_session",
        "freecad.pad_sketch",
        "freecad.pocket_sketch",
        "freecad.export_model",
        "twin.commit_geometry",
    ):
        assert tool in tools, tool


# --- the routing -------------------------------------------------------------


@pytest.mark.asyncio
async def test_hardware_v1_routes_design_electronics_firmware_native_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.runs import routes

    monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: SimpleNamespace())
    monkeypatch.setattr("api_gateway.runs.routes.ensure_usage_store", lambda: None)
    monkeypatch.setattr("api_gateway.projects.routes.get_project_backend", lambda: object())
    monkeypatch.setattr("api_gateway.twin.routes.get_twin", lambda: object())

    hw = await routes.build_phase_brain("r1", "hardware_v1")
    assert isinstance(hw._handlers["design"], NativeMechanicalDesignHandler)
    elec, fw = hw._handlers["electronics"], hw._handlers["firmware"]
    assert isinstance(elec, NativeFirstPhaseHandler) and elec.required_types == (
        "schematic",
        "pcb_layout",
    )
    assert isinstance(fw, NativeFirstPhaseHandler) and fw.required_types == ("firmware_source",)
    # the deterministic handlers stay as the backstops
    assert type(elec.fallback).__name__ == "GoalDrivenElectronicsHandler"
    assert type(fw.fallback).__name__ == "GoalDrivenFirmwareHandler"
