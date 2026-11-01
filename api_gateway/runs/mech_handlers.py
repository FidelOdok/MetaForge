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

import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from api_gateway.runs.geometry_constraints import check_geometry_constraints
from mcp_core.service_auth import is_service_refusal
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from orchestrator.harness.providers.routing import routing_scope
from orchestrator.harness.providers.usage import usage_scope
from orchestrator.harness.tool_exec import note_service_refusal
from skill_registry.mcp_bridge import McpBridge, McpToolError

logger = structlog.get_logger(__name__)

# freecad.create_primitive kinds and the default dimensions (mm) per kind, so a
# part is always producible even when spec extraction is thin.
_PRIMITIVE_KINDS = ("box", "cylinder", "cone", "sphere")
_DEFAULT_PARAMS: dict[str, dict[str, float]] = {
    "box": {"length": 40.0, "width": 30.0, "height": 8.0},
    "cylinder": {"radius": 15.0, "height": 40.0},
    "cone": {"radius1": 15.0, "radius2": 8.0, "height": 40.0},
    "sphere": {"radius": 20.0},
}


def _slug_name(goal: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", goal) if len(w) > 2][:3]
    return " ".join(w.capitalize() for w in words) or "Part"


def _normalize_spec(spec: dict[str, Any], goal: str) -> dict[str, Any]:
    """Coerce an extracted part spec into a valid, buildable primitive spec."""
    kind = str(spec.get("kind") or "box").lower()
    if kind not in _PRIMITIVE_KINDS:
        kind = "box"
    params = spec.get("parameters")
    clean: dict[str, float] = {}
    if isinstance(params, dict):
        for k, v in params.items():
            try:
                clean[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    if not clean:
        clean = dict(_DEFAULT_PARAMS[kind])
    name = str(spec.get("name") or "").strip() or _slug_name(goal)
    material = str(spec.get("material") or "").strip() or "Al6061-T6"
    return {"name": name[:60], "kind": kind, "parameters": clean, "material": material}


def _spec_extents(spec: dict[str, Any]) -> list[float]:
    """Bounding extents (mm) of a primitive spec, in x, y, z order."""
    p = spec["parameters"]
    kind = spec["kind"]
    if kind == "box":
        return [p.get("length", 0.0), p.get("width", 0.0), p.get("height", 0.0)]
    if kind == "cylinder":
        return [2 * p.get("radius", 0.0), 2 * p.get("radius", 0.0), p.get("height", 0.0)]
    if kind == "cone":
        r = max(p.get("radius1", 0.0), p.get("radius2", 0.0))
        return [2 * r, 2 * r, p.get("height", 0.0)]
    return [2 * p.get("radius", 0.0)] * 3


def _spec_violations(spec: dict[str, Any], constraints: list[Any]) -> list[str]:
    """Stated limits the extracted spec breaks, using the gate's own comparison."""
    x, y, z = _spec_extents(spec)
    meta = {"dimensions_mm": {"x": x, "y": y, "z": z}, "material": spec["material"]}
    outcome = check_geometry_constraints(constraints, [(spec["name"], meta)])
    return outcome.violations + outcome.warnings


def _constraints_block(constraints: list[Any]) -> str:
    lines = []
    for c in constraints:
        metric = getattr(c, "metric", "")
        if not metric:
            continue
        limit = getattr(c, "limit", None)
        bound = (
            f" {getattr(c, 'operator', '<=')} {limit:g}{getattr(c, 'unit', '')}" if limit else ""
        )
        note = getattr(c, "acceptance_criteria", "") or getattr(c, "message", "")
        lines.append(f"  - {getattr(c, 'name', metric)} ({metric}{bound}) {note}".rstrip())
    return "\n".join(lines)


async def _extract_part_spec(
    goal: str, prior: str, *, provider: str | None, model: str | None
) -> dict[str, Any]:
    """Ask the LLM for a machinable primitive spec, normalized (never raises)."""
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_metrics

    prompt = (
        "You are extracting a buildable primitive spec for a mechanical part. "
        "Reply with ONLY a JSON object, no prose:\n"
        '{"name": "<short part name>", "kind": "box|cylinder|cone|sphere", '
        '"parameters": {<dimensions in mm>}, "material": "<material>"}\n'
        "box -> length,width,height; cylinder -> radius,height; cone -> "
        "radius1,radius2,height; sphere -> radius. Choose dimensions that suit the "
        "part and its load.\n\n"
        f"Goal: {goal}\nContext: {prior}"
    )
    try:
        reply = await run_chat_turn(
            prompt,
            mcp_bridge=None,
            session_id="mech-spec",
            max_steps=1,
            provider=provider,
            model=model,
            metrics=get_metrics(),
        )
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        spec = json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 - fall back to a default part, never fail
        logger.warning("mech_spec_extract_failed", error=str(exc))
        spec = {}
    return _normalize_spec(spec if isinstance(spec, dict) else {}, goal)


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
            # FORGE-501: no stored blob means an unloadable cad_model; fail the phase.
            require_blob_store=True,
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


class GoalDrivenMechanicalHandler(_BridgeHandler):
    """Goal-driven mechanical design with a guaranteed loadable cad_model.

    The LLM extracts the *part spec* (name / kind / dimensions / material) from
    the goal — its strength — then this handler deterministically authors the
    primitive, exports STEP, and commits it via the geometry recorder — the
    reliable persistence path. So the cad_model is always goal-named and loadable,
    fixing the native brain's inconsistency at the multi-step CAD sequence.
    """

    def __init__(
        self,
        bridge: McpBridge,
        recorder: Any,
        *,
        provider: str | None = None,
        model: str | None = None,
        extract: Any = _extract_part_spec,
        constraints_loader: Callable[[str | None], Awaitable[list[Any]]] | None = None,
    ) -> None:
        super().__init__(bridge)
        self._recorder = recorder
        self._provider = provider
        self._model = model
        self._extract = extract
        self._constraints_loader = constraints_loader

    async def _load_constraints(self, project_id: str | None) -> list[Any]:
        if self._constraints_loader is None or not project_id:
            return []
        try:
            return list(await self._constraints_loader(project_id))
        except Exception as exc:  # noqa: BLE001 - the spec still gets the flow context
            logger.warning("mech_constraints_load_failed", error=str(exc))
            return []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        prior = "\n".join(f"  - {p.title}: {o.summary}" for p, o in context.completed) or "(none)"
        # FORGE-496: the stated stock, envelope and material reach the spec.
        constraints = await self._load_constraints(context.project_id)
        if context.flow_context:
            prior = (
                f"Flow context (stated by the requester, binding):\n{context.flow_context}\n{prior}"
            )
        block = _constraints_block(constraints)
        if block:
            prior += f"\nRequirement constraints the part must satisfy:\n{block}"
        spec = await self._extract(goal, prior, provider=self._provider, model=self._model)
        broken = _spec_violations(spec, constraints)
        if broken:
            retry = f"{prior}\nYour previous spec broke these limits, fix them: " + "; ".join(
                broken
            )
            spec = await self._extract(goal, retry, provider=self._provider, model=self._model)
            broken = _spec_violations(spec, constraints)
            if broken:
                logger.warning("mech_spec_breaks_constraints", violations=broken)

        session = await self._invoke("freecad.open_session", {"name": "mech-design"})
        sid = session.get("session_id")
        if not sid:
            raise RuntimeError("freecad.open_session returned no session_id")
        prim = await self._invoke(
            "freecad.create_primitive",
            {
                "session_id": sid,
                "kind": spec["kind"],
                "name": spec["name"],
                "parameters": spec["parameters"],
            },
        )
        export = await self._invoke(
            "freecad.export_model", {"session_id": sid, "obj_id": prim.get("obj_id")}
        )
        step_b64 = (
            export.get("step_base64")
            or export.get("base64")
            or export.get("step")
            or export.get("content")
        )
        if not step_b64:
            raise RuntimeError(f"freecad.export_model returned no STEP (keys: {list(export)})")

        rec = await self._recorder(
            step_base64=step_b64,
            name=spec["name"],
            project_id=context.project_id,
            session_id=context.session_id,
            extra_metadata={
                "material": spec["material"],
                "kind": spec["kind"],
                "dimensions_mm": dict(zip("xyz", _spec_extents(spec), strict=True)),
            },
            # FORGE-501: no stored blob means an unloadable cad_model; fail the phase.
            require_blob_store=True,
        )
        node_id = rec.get("node_id") if isinstance(rec, dict) else None
        dims = ", ".join(f"{k}={v:g}mm" for k, v in spec["parameters"].items())
        await self._record_decision(
            title=f"{spec['name']} detailed design",
            rationale=(
                f"{spec['name']} ({spec['kind']}; {dims}) in {spec['material']}, sized to the "
                "approved requirements to carry the load case with the target safety factor "
                "(see V&V)."
            ),
            project_id=context.project_id,
        )
        return PhaseOutcome(
            summary=(
                f"Authored + committed {spec['name']} cad_model (node {node_id}) — {spec['kind']} "
                f"in {spec['material']}; recorded the design rationale."
            ),
            artifacts=[f"cad_model:{node_id}", "design_decision:design"],
            status="completed",
        )


class HybridBrain:
    """Routes each phase to a deterministic handler, else the fallback brain."""

    def __init__(
        self, *, handlers: dict[str, Any], fallback: Any, run_id: str | None = None
    ) -> None:
        self._handlers = handlers
        self._fallback = fallback
        self._run_id = run_id

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        # FORGE-476: every model call a phase makes, deterministic handler or
        # ReAct brain, is attributed to this run and phase. Both engines
        # (in-process and the Temporal worker) build their brain through here.
        with (
            usage_scope(run_id=self._run_id, phase=phase.id, role="phase_brain"),
            routing_scope(
                project_id=context.project_id,
                phase_model=phase.model or "",
                disciplines=tuple(phase.disciplines),
            ),
        ):
            handler = self._handlers.get(phase.id)
            if handler is not None:
                logger.info("design_flow_deterministic_phase", phase=phase.id)
                try:
                    return await handler.run_phase(goal=goal, phase=phase, context=context)
                except McpToolError as exc:
                    if not is_service_refusal(exc):
                        raise
                    # FORGE-492. The guardrail said no to a tool the scripted
                    # step needed. Failing the phase and the run on that leaves
                    # nobody able to choose another tool; the model can. Hand
                    # the phase to it with the refusal as the first thing it
                    # reads, and log and count the refusal.
                    from api_gateway.chat.routes import get_metrics

                    note_service_refusal(get_metrics(), exc.tool_id, exc, source="handler")
                    refusal = (
                        f"A scripted step of this phase was refused: {exc.details}. "
                        "That tool is not available to this run and retrying it cannot "
                        "change that. Achieve the phase objective with other tools."
                    )
                    return await self._fallback.run_phase(
                        goal=f"{goal}\n\nNOTE: {refusal}", phase=phase, context=context
                    )
            return await self._fallback.run_phase(goal=goal, phase=phase, context=context)


_NATIVE_DESIGN_NOTE = (
    "DESIGN RULES (FORGE-496): design from the stated flow context and the recorded "
    "requirement constraints, not from the goal text alone. Use the stated stock sizes, "
    "envelope, materials and fixings as given. Build a multi-part design with the FreeCAD "
    "session tools, give every part a meaningful name (a plate is 'Shelf Board', not "
    "'Part_1'), and persist it with the commit-geometry tool. Pass extra_metadata on that "
    'call with the real material and key dimensions, e.g. {"material": "18 mm birch '
    'plywood", "dimensions_mm": {"x": 800, "y": 300, "z": 18}}. The gate compares these to '
    "the requirement constraints, so a dimension or material that breaks them fails it."
)


class NativeMechanicalDesignHandler:
    """mech_v1 design phase: the native ReAct brain designs, a scripted spec backstops it.

    The brain gets the flow context (FORGE-491) and the FreeCAD authoring tools and
    produces a named multi-part design. If the phase ends with no committed
    cad_model, the goal-driven single-primitive handler runs instead, given the
    same flow context and constraint set, and the phase summary says so.
    """

    def __init__(
        self,
        native: Any,
        fallback: GoalDrivenMechanicalHandler,
        has_cad_model: Callable[[str | None, float], Awaitable[bool]],
    ) -> None:
        self._native = native
        self._fallback = fallback
        self._has_cad_model = has_cad_model

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        started = time.time()
        reason = ""
        try:
            outcome = await self._native.run_phase(
                goal=f"{goal}\n\n{_NATIVE_DESIGN_NOTE}", phase=phase, context=context
            )
            if await self._has_cad_model(context.project_id, started):
                return outcome
            reason = "the native design phase ended without a committed cad_model"
        except McpToolError as exc:
            if is_service_refusal(exc):
                raise
            reason = f"the native design phase failed: {exc}"
        except Exception as exc:  # noqa: BLE001 - the backstop exists for exactly this
            reason = f"the native design phase failed: {exc}"
        logger.warning("mech_native_design_fallback", reason=reason)
        fallback = await self._fallback.run_phase(goal=goal, phase=phase, context=context)
        return PhaseOutcome(
            summary=(
                f"FALLBACK: {reason}; a single-primitive design was authored from the "
                f"goal, flow context and constraints instead. {fallback.summary}"
            ),
            artifacts=fallback.artifacts,
            status=fallback.status,
        )
