"""A design-flow phase that runs the tool-using brain first, a scripted handler as backstop.

hardware_v1 ran its electronics and firmware phases on deterministic handlers
that never call KiCad or the firmware build: the electronics handler records a
BOM and a power budget as a decision, the firmware handler a pin map and a
scaffold. No hardware_v1 run could therefore produce a schematic, a board or
firmware that builds, however capable the model (2026-11-15, a USB-C night
light). This is the FORGE-496 pattern mech_v1's design phase already uses,
generalised: the native ReAct brain does the phase with its tools; a probe
checks the work products the phase must leave in the twin; when they are
missing, or the brain fails, the scripted handler runs and the summary starts
with FALLBACK so the gate reviewer sees which one produced the phase.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from mcp_core.service_auth import is_service_refusal
from observability.tracing import get_tracer
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from skill_registry.mcp_bridge import McpToolError

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.native_first")

#: (project_id, since_ts) -> the work-product types recorded since then.
PresentTypes = Callable[[str | None, float], Awaitable[set[str]]]

ELECTRONICS_NOTE = (
    "ELECTRONICS RULES: produce the real design, not a description of one. Prefer complete "
    "commercial modules and boards (a dev board with USB-C, an LED ring board, a sensor "
    "breakout) over a custom board whenever they meet the requirements and fit; record why "
    "any module was rejected. Use the parts the architecture and concept phases chose. Draw "
    "the circuit with kicad.generate_schematic (for a module build, the interconnect: each "
    "board as its connector or header symbol, nets as REF.PIN) and keep ERC clean "
    "(kicad.run_erc). Only when the concept calls for a custom board: lay it out with "
    "kicad.generate_pcb inside the approved outline, route it with kicad.autoroute_pcb, run "
    "kicad.run_drc until it is clean, and export Gerbers. Record the files with "
    "twin.record_design_files (work_product_type schematic; pcb_layout and gerber for a "
    "board). Close the power budget per rail with power.check_budget, record each chosen part "
    "with twin.record_component_selection, and finish with one twin.record_decision that "
    "states the ERC (and DRC) results as numbers. A check that did not run is reported as "
    "not run, never as passed."
)

FIRMWARE_NOTE = (
    "FIRMWARE RULES: produce firmware that builds, not a description of it. Write the source "
    "for the chosen MCU and framework from the pin map and the board's nets, build it with "
    "firmware.build, run firmware.static_check on it, and run it with firmware.run_emulated "
    "on the emulator target the requirements name, with uart_expect lines that prove the "
    "behaviour. Record the source with twin.record_design_files (work_product_type "
    "firmware_source, and pinmap), and finish with one twin.record_decision that gives the "
    "build size, the static-check findings and the emulator verdict as they were returned. "
    "Hardware the emulator cannot model (touch, sensors) is mocked behind an interface and "
    "said so."
)


class NativeFirstPhaseHandler:
    """Run the native brain; keep its outcome only if the phase's work products exist."""

    def __init__(
        self,
        native: Any,
        fallback: Any,
        present_types: PresentTypes,
        *,
        required_types: tuple[str, ...],
        note: str,
        label: str,
    ) -> None:
        self.native = native
        self.fallback = fallback
        self.present_types = present_types
        self.required_types = required_types
        self.note = note
        self.label = label

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        started = time.time()
        with tracer.start_as_current_span("design_flow.native_first") as span:
            span.set_attribute("phase.id", phase.id)
            span.set_attribute("phase.label", self.label)
            try:
                outcome = await self.native.run_phase(
                    goal=f"{goal}\n\n{self.note}", phase=phase, context=context
                )
                present = await self.present_types(context.project_id, started)
                missing = [t for t in self.required_types if t not in present]
                if not missing:
                    span.set_attribute("phase.engine", "native")
                    logger.info("native_first_phase_kept", phase=phase.id, label=self.label)
                    return outcome
                reason = f"the native {self.label} phase ended without {', '.join(missing)}"
            except McpToolError as exc:
                if is_service_refusal(exc):
                    raise
                span.record_exception(exc)
                reason = f"the native {self.label} phase failed: {exc}"
            except Exception as exc:  # noqa: BLE001 - the backstop exists for exactly this
                span.record_exception(exc)
                reason = f"the native {self.label} phase failed: {exc}"
            span.set_attribute("phase.engine", "fallback")
            logger.warning("native_first_phase_fallback", phase=phase.id, reason=reason)
            fallback = await self.fallback.run_phase(goal=goal, phase=phase, context=context)
            return PhaseOutcome(
                summary=(
                    f"FALLBACK: {reason}; the scripted {self.label} handler ran instead. "
                    f"{fallback.summary}"
                ),
                artifacts=fallback.artifacts,
                status=fallback.status,
            )
