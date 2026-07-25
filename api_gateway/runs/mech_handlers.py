"""Deterministic mechanical phase handlers for the design flow (MET-10).

Where the ReAct brain is unreliable at driving multi-step CAD/FEA, these
scripted handlers guarantee each phase's required deliverable actually lands in
the twin. They implement the same ``PhaseBrain.run_phase`` shape, so a
:class:`HybridBrain` can route a phase to a handler and fall back to ReAct for
phases without one.

Handlers talk to the same MCP bridge + geometry recorder the rest of the
gateway uses, so their outputs are ordinary, viewable twin work products.
"""

from __future__ import annotations

from typing import Any

import structlog

from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)

# Hip-bracket first-pass sizing (mm) and load case — kept here so the design and
# simulation handlers agree on the same numbers.
_BRACKET = {"length": 40.0, "width": 30.0, "height": 8.0}
_MATERIAL = "Al6061-T6"
_YIELD_MPA = 276.0
_YOUNGS_GPA = 68.9
_LOAD_N = 100.0  # 5 kg body * 9.81 * ~2x dynamic, single-leg stance


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    """Unwrap an MCP result envelope to its ``data`` payload, raising on error."""
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RuntimeError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


class _BridgeHandler:
    def __init__(self, bridge: McpBridge) -> None:
        self._bridge = bridge

    async def _invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return _data(await self._bridge.invoke(tool, args), tool)

    async def _record_decision(
        self,
        *,
        title: str,
        rationale: str,
        project_id: str | None,
        alternatives: list | None = None,
    ) -> None:
        args: dict[str, Any] = {"title": title, "rationale": rationale}
        if project_id:
            args["project_id"] = project_id
        if alternatives:
            args["alternatives"] = alternatives
        await self._invoke("twin.record_decision", args)


class RequirementsHandler(_BridgeHandler):
    """Records the requirements + load case as a design decision."""

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        rationale = (
            f"Requirements for: {goal}. Functional: carry the body load through the hip "
            f"mount in single-leg stance. Load case: {_LOAD_N:.0f} N at the mount "
            f"(5 kg body x 9.81 x ~2x dynamic factor). Constraints: material {_MATERIAL} "
            f"(yield {_YIELD_MPA:.0f} MPa), target safety factor >= 2, bracket envelope "
            f"~{_BRACKET['length']:.0f}x{_BRACKET['width']:.0f}x{_BRACKET['height']:.0f} mm."
        )
        await self._record_decision(
            title="Quadruped hip-mount requirements + load case",
            rationale=rationale,
            project_id=context.project_id,
            alternatives=[
                {
                    "option": "Carbon-composite bracket",
                    "reason_rejected": "Out of scope for Phase-1 metal FEA.",
                },
            ],
        )
        return PhaseOutcome(
            summary=(
                f"Established requirements: {_LOAD_N:.0f} N mount load, {_MATERIAL}, "
                "safety factor >= 2; recorded as a design decision."
            ),
            artifacts=["design_decision:requirements"],
            status="completed",
        )


class MechanicalDesignHandler(_BridgeHandler):
    """Authors the hip bracket and commits it as a viewable cad_model."""

    def __init__(self, bridge: McpBridge, recorder: Any) -> None:
        super().__init__(bridge)
        self._recorder = recorder

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        session = await self._invoke("freecad.open_session", {"name": "quadruped-design"})
        sid = session.get("session_id")
        if not sid:
            raise RuntimeError("freecad.open_session returned no session_id")
        prim = await self._invoke(
            "freecad.create_primitive",
            {
                "session_id": sid,
                "kind": "box",
                "name": "HipBracket_FL",
                "parameters": dict(_BRACKET),
            },
        )
        obj_id = prim.get("obj_id")
        export = await self._invoke("freecad.export_model", {"session_id": sid, "obj_id": obj_id})
        step_b64 = (
            export.get("step_base64")
            or export.get("base64")
            or export.get("step")
            or export.get("content")
        )
        if not step_b64:
            raise RuntimeError(
                f"freecad.export_model returned no base64 STEP (keys: {list(export)})"
            )

        rec = await self._recorder(
            step_base64=step_b64,
            name="HipBracket_FL",
            project_id=context.project_id,
            session_id=context.session_id,
            extra_metadata={"material": _MATERIAL, "load_case_N": _LOAD_N},
        )
        node_id = rec.get("node_id") if isinstance(rec, dict) else None
        await self._record_decision(
            title="Hip bracket detailed design",
            rationale=(
                f"First-pass hip mount bracket {_BRACKET['length']:.0f}x{_BRACKET['width']:.0f}"
                f"x{_BRACKET['height']:.0f} mm in {_MATERIAL}, sized to keep bending stress "
                "under the single-leg-stance load below yield with SF >= 2 (see V&V)."
            ),
            project_id=context.project_id,
        )
        return PhaseOutcome(
            summary=(
                f"Authored + committed hip bracket cad_model (node {node_id}) in {_MATERIAL}; "
                "recorded the design rationale."
            ),
            artifacts=[f"cad_model:{node_id}", "design_decision:design"],
            status="completed",
        )


class SimulationHandler(_BridgeHandler):
    """First-order stress check + safety-factor verdict, recorded as a decision.

    Uses a closed-form cantilever-bending estimate (real CalculiX FEA is a
    follow-up pending the freecad mesh-adapter fix). Deterministic and honest:
    the verdict is a hand-calc, labelled as such.
    """

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        # Cantilever bending of the bracket cross-section under the mount load.
        b = _BRACKET["width"]  # mm
        h = _BRACKET["height"]  # mm
        arm = _BRACKET["length"]  # mm moment arm
        moment = _LOAD_N * arm  # N*mm
        section_mod = (b * h * h) / 6.0  # mm^3, rectangular section
        sigma_mpa = moment / section_mod  # N/mm^2 = MPa
        safety = _YIELD_MPA / sigma_mpa if sigma_mpa else float("inf")
        verdict = "PASS" if safety >= 2.0 else "FAIL"

        rationale = (
            f"First-order cantilever bending check (hand-calc, not FEA): load {_LOAD_N:.0f} N "
            f"at {arm:.0f} mm -> M={moment:.0f} N.mm; section modulus Z={section_mod:.0f} mm^3; "
            f"sigma_max={sigma_mpa:.1f} MPa vs yield {_YIELD_MPA:.0f} MPa -> safety factor "
            f"{safety:.2f}. Requirement SF>=2 -> {verdict}. CalculiX FEA to confirm once the "
            "mesh adapter is fixed."
        )
        await self._record_decision(
            title=f"Hip bracket V&V verdict: {verdict} (SF {safety:.2f})",
            rationale=rationale,
            project_id=context.project_id,
        )
        return PhaseOutcome(
            summary=(
                f"V&V hand-calc: sigma={sigma_mpa:.1f} MPa, safety factor {safety:.2f} -> "
                f"{verdict} against SF>=2; recorded as a decision."
            ),
            artifacts=["design_decision:vv"],
            status="completed",
        )


class HybridBrain:
    """Routes each phase to a deterministic handler, else the fallback brain."""

    def __init__(self, *, handlers: dict[str, Any], fallback: Any) -> None:
        self._handlers = handlers
        self._fallback = fallback

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        handler = self._handlers.get(phase.id)
        if handler is not None:
            logger.info("design_flow_deterministic_phase", phase=phase.id)
            return await handler.run_phase(goal=goal, phase=phase, context=context)
        return await self._fallback.run_phase(goal=goal, phase=phase, context=context)
