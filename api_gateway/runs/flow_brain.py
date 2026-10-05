"""Production phase brain for the design-flow executor (MET-10).

Wraps the chat ReAct harness (``run_chat_turn``) so each design-flow phase is
executed by the LLM brain with the full MCP tool surface (project / twin / CAD /
FEA / EDA / knowledge). Per ADR-008 the reasoning lives in the external harness;
this adapter is the thin bridge that hands a phase a scoped prompt and lets it
drive tools to produce + record artifacts into the digital twin.

Lives in ``api_gateway`` (layer 4) because it depends on the chat harness and
the MCP bridge; the executor it plugs into stays pure in ``orchestrator``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

from api_gateway.chat.harness_backend import design_flow_approval_timeout_seconds, run_chat_turn
from api_gateway.chat.routes import get_metrics
from mcp_core.context import ItemSlotClaim, McpCallContext, current_context, with_context
from mcp_core.profiles import phase_overflow, tools_for_phase, unmapped_disciplines
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.slots import effective_slots, slots_brief
from orchestrator.design_flow.spec import DeliverableSlot, Phase
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_context import (
    SkillCard,
    cards_for_domains,
    load_skill_cards,
    procedural_overlay,
)

logger = structlog.get_logger(__name__)

#: Disciplines already reported as having no tool profile, so the debug line
#: appears once per process rather than once per phase.
_UNMAPPED_LOGGED: set[str] = set()


def _phase_deliverables(phase: Phase) -> tuple[str, ...]:
    """Every deliverable type the phase must or is expected to produce (FORGE-497)."""
    return tuple(dict.fromkeys([*phase.required_deliverables, *phase.expected_artifacts]))


def _report_phase_tools(phase: Phase) -> frozenset[str]:
    """The phase's tool allowlist, with anything dropped or unmapped made visible."""
    deliverables = _phase_deliverables(phase)
    dropped = phase_overflow(phase.disciplines, deliverables)
    if dropped:
        logger.warning(
            "design_flow_phase_tools_dropped",
            phase=phase.id,
            disciplines=list(phase.disciplines),
            dropped_tools=dropped,
        )
    for d in unmapped_disciplines(phase.disciplines):
        if d.lower() not in _UNMAPPED_LOGGED:
            _UNMAPPED_LOGGED.add(d.lower())
            logger.debug("design_flow_discipline_has_no_tool_profile", discipline=d, phase=phase.id)
    return tools_for_phase(phase.disciplines, deliverables)


#: The twin work-product type each hint produces is the key, spelled exactly as
#: ``gate_eval.ProjectGateEvaluator.present_types`` reports it (the
#: ``WorkProductType`` value), so following a hint satisfies the gate.
def deliverable_hints(pid: str) -> dict[str, str]:
    """Per-deliverable "which tool, which arguments" hints (FORGE-494).

    Every type a template requires or expects, or that tailoring's
    ``add_deliverable`` is likely to add, has an entry; a type with no
    model-callable recorder says so instead of inviting a wrong-typed record.
    """
    no_tool = (
        "no MCP tool records this type, the platform's own phase handler produces it; "
        "do NOT record a different type (decision, entity, documentation) in its place"
    )
    return {
        "design_decision": (
            "record it with the record-decision tool (title, rationale, alternatives), "
            "and depends_on=[the constraint set's item_ref] when it rests on requirements "
            "(link them, do not restate their values), "
            f"project_id={pid}"
        ),
        "intent": (
            "record ONE with the record-engineering-entity tool "
            "(entity_type='intent', statement=why this product exists, "
            f"give it a short title so later phases can reference it), project_id={pid}"
        ),
        "stakeholder_need": (
            "record at least one with the record-engineering-entity tool "
            "(entity_type='stakeholder_need', statement=what the stakeholder needs, "
            "parent_refs=[the intent's title], relation='motivates'), "
            f"project_id={pid}"
        ),
        "cad_model": (
            "author the geometry with the FreeCAD authoring tools, then PERSIST it "
            f"with the commit-geometry tool (project_id={pid}) so it becomes a "
            "viewable cad_model in the twin — a described-but-uncommitted model does "
            "NOT count. If the design has MORE THAN ONE part, commit each part as its own "
            "named cad_model, then ALSO build ONE assembly: freecad.create_assembly, "
            "freecad.add_part_to_assembly for every part by its name, export it with "
            "freecad.export_model, and commit it as '<product> Assembly' with "
            "twin.commit_geometry passing parts=[{node_id: the part cad_model's node id, "
            "name, material, position_bbox_mm}, ...]; the gate fails a multi-part "
            "design that has no assembly, or whose part boxes overlap"
        ),
        "prd": (
            "record the product requirements document with the record-document tool "
            "(document_type='prd', name=its title, content=the prose: background, scope, "
            "out of scope; requirement values go in the constraint set, not the prd), "
            f"project_id={pid}; recording it as an engineering entity or a "
            "decision does NOT count"
        ),
        "constraint_set": (
            "record the quantified requirements with the record-constraint-set tool "
            "(title, constraints=[{name, metric, operator, limit, unit}, ...], at least "
            f"one entry), project_id={pid}; one call creates the constraint_set"
        ),
        "simulation_result": (
            "stage each committed cad_model you analyse with the stage-work-product-file "
            "tool (work_product_id=its node id) to get a local STEP file_path, mesh that "
            "path with the freecad generate-mesh tool, "
            "run the analysis (calculix run-fea, then extract-results), then record the "
            "outcome with the record-document tool (document_type='simulation_result', "
            "name, content=the extract-results JSON summary, metadata=the same summary "
            "fields, source_part_node_ids=[the analysed cad_model node id]); also record "
            "the load basis (service_load_n, factored_load_n, and which load the run "
            "applied) so the gate can scale it to a service-load limit, and, optionally, "
            "metadata.modelling_assumptions (e.g. bonded vs contact joints, material "
            "approximations), which the reviewer sees at the gate, "
            f"project_id={pid}"
        ),
        "load_case": (
            "record it with the record-document tool (document_type='load_case', name, "
            "content=JSON of the boundary conditions, metadata={material, fixed_node_set, "
            f"load_node_set, load_force_n=[x, y, z]}}), project_id={pid}"
        ),
        "documentation": (
            "record it with the record-document tool "
            f"(document_type='documentation', name, content=markdown), project_id={pid}"
        ),
        "robot_description": (
            "record the URDF/SDF export with the record-document tool "
            "(document_type='robot_description', name, content=the export text, "
            "format='urdf', source_part_node_ids=[the cad_model node ids]), "
            f"project_id={pid}"
        ),
        "bom": (
            "record each chosen part with the record-component-selection tool "
            "(mpn, manufacturer, category, purchase_unit, quantity), "
            f"project_id={pid}; the calls build the project's bom"
        ),
        "pinmap": (
            "derive it with the create-firmware-scaffold tool "
            f"(work_product_id=a work product with metadata.assembly.joints), project_id={pid}; "
            "the same call also records the firmware_source"
        ),
        "firmware_source": (
            "derive it with the create-firmware-scaffold tool "
            f"(work_product_id=a work product with metadata.assembly.joints), project_id={pid}; "
            "the same call also records the pinmap"
        ),
        "schematic": f"{no_tool} (Phase 1 KiCad is read-only)",
        "pcb_layout": f"{no_tool} (Phase 1 KiCad is read-only)",
        "gerber": f"{no_tool}; the kicad export-gerber tool writes a file, not a twin node",
        "pick_and_place": no_tool,
        "manufacturing_file": no_tool,
        "test_plan": (
            f"{no_tool}; the generate-test-plan tool records verification_case entities, "
            "not a test_plan"
        ),
        "test_result": no_tool,
        "verification_report": no_tool,
    }


#: FORGE-501: per-phase tool-use budget. A phase building several sketch-based
#: parts ran out of the old hard-coded 24 before committing a cad_model.
DEFAULT_PHASE_MAX_STEPS = 24
DEFAULT_PHASE_MAX_STEPS_HEAVY = 60
#: Deliverables whose authoring is a long chain of CAD/solver calls.
HEAVY_DELIVERABLES = frozenset({"cad_model", "simulation_result"})


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("flow_phase_budget_env_invalid", var=name, value=raw)
        return default
    if value < 1:
        logger.warning("flow_phase_budget_env_invalid", var=name, value=raw)
        return default
    return value


def phase_is_heavy(phase: Phase) -> bool:
    """True when the phase must or may produce a cad_model or simulation_result."""
    wanted = {*phase.required_deliverables, *phase.expected_artifacts}
    return bool(wanted & HEAVY_DELIVERABLES)


def phase_step_budget(phase: Phase, override: int | None = None) -> int:
    """The tool-use budget for ``phase``.

    An explicit ``override`` (constructor ``max_steps``) wins. Otherwise
    ``METAFORGE_FLOW_PHASE_MAX_STEPS_HEAVY`` (default 60) for heavy phases and
    ``METAFORGE_FLOW_PHASE_MAX_STEPS`` (default 24) for the rest.
    """
    if override is not None:
        return override
    if phase_is_heavy(phase):
        return _env_int("METAFORGE_FLOW_PHASE_MAX_STEPS_HEAVY", DEFAULT_PHASE_MAX_STEPS_HEAVY)
    return _env_int("METAFORGE_FLOW_PHASE_MAX_STEPS", DEFAULT_PHASE_MAX_STEPS)


_SENTINEL_SESSION = uuid.UUID(int=0)


@contextmanager
def phase_slot_scope(
    slots: tuple[DeliverableSlot, ...], project_id: str | None
) -> Iterator[McpCallContext]:
    """Put the phase's slots on the MCP call context (FORGE-524).

    The recorders read them (``item_revisions.phase_slots``) the same way they
    read ``run_id``: the Temporal worker's phase scope already carries the run
    and project, and this adds the slots to it. With no scope installed (the
    in-process engine), one is created for the run's project, so its writes
    resolve to slots too. No slots, no change: the context is left as it is.
    """
    ctx = current_context()
    if not slots:
        yield ctx
        return
    claims = tuple(
        ItemSlotClaim(item_type=s.item_type, name=s.name, item_key=s.item_key) for s in slots
    )
    if ctx.session_id == _SENTINEL_SESSION:
        try:
            pid = uuid.UUID(str(project_id)) if project_id else None
        except ValueError:
            pid = None
        scoped = McpCallContext(project_id=pid, item_slots=claims)
    else:
        scoped = ctx.model_copy(update={"item_slots": claims})
    with with_context(scoped):
        yield scoped


class ReActPhaseBrain:
    """A :class:`~orchestrator.design_flow.executor.PhaseBrain` backed by ReAct.

    ``max_steps`` pins the tool-use budget for every phase; left unset the
    budget is per phase (:func:`phase_step_budget`). ``provider``/``model``
    override the env defaults (else the gateway's configured LLM is used).
    """

    def __init__(
        self,
        *,
        mcp_bridge: McpBridge | None,
        session_id: str = "design-flow",
        max_steps: int | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._bridge = mcp_bridge
        self._session_id = session_id
        self._max_steps = max_steps
        self._provider = provider
        self._model = model
        # Load the SKILL.md corpus once; each phase injects the procedures for
        # its disciplines (the "select" pillar — procedural context).
        self._cards: list[SkillCard] = load_skill_cards()

    def _prompt(self, goal: str, phase: Phase, context: FlowContext) -> str:
        prior = (
            "\n".join(f"  - {p.title}: {o.summary}" for p, o in context.completed)
            or "  (none yet — this is the first phase)"
        )
        project_line = (
            f"Project id: {context.project_id} — scope every tool call to this project "
            f"(pass project_id where accepted) and record artifacts to its digital twin."
            if context.project_id
            else "No project id supplied; still record decisions to the twin."
        )
        expected = ", ".join(phase.expected_artifacts) or "the appropriate work products"
        deliverables = self._deliverable_guidance(phase, context)
        # FORGE-524: the item keys this phase's writes land on.
        items_brief = slots_brief(effective_slots(phase))
        items_block = f"{items_brief}\n" if items_brief else ""
        overlay = procedural_overlay(cards_for_domains(self._cards, phase.disciplines))
        overlay_block = f"{overlay}\n" if overlay else ""
        # FORGE-491: the flow's stated context leads the prompt, identical for
        # every phase, so it is part of the cacheable prefix.
        flow_block = (
            "Flow context (stated by the requester; treat as given, not unknown):\n"
            f"{context.flow_context}\n\n"
            if context.flow_context
            else ""
        )
        # FORGE-495: on a retry the gate's findings and the reviewer's reason
        # come before everything else, so they cannot be read past.
        retry_block = f"{context.retry_feedback}\n\n" if context.retry_feedback else ""
        return (
            f"{retry_block}"
            f"{flow_block}"
            f"You are MetaForge's autonomous design engineer executing the "
            f"**{phase.title}** phase of a gated design flow.\n\n"
            f"Product goal: {goal}\n"
            f"{project_line}\n\n"
            f"Prior phases completed:\n{prior}\n\n"
            f"Your objective for THIS phase:\n{phase.objective}\n\n"
            f"{deliverables}\n"
            f"{items_block}"
            f"{overlay_block}"
            f"Use the available MCP tools (project, twin, CAD/FEA/EDA, knowledge) to "
            f"actually perform the work and record {expected} into the digital twin — "
            f"do not just describe it. Work efficiently: prefer one decisive tool call "
            f"per step and avoid repeating a failed call with the same arguments. When "
            f"done, reply with a concise summary (3-5 sentences) of what you produced "
            f"and the specific artifacts/decisions you recorded, so a human reviewer "
            f"can decide whether to pass this gate."
        )

    @staticmethod
    def _deliverable_guidance(phase: Phase, context: FlowContext) -> str:
        """Tell the brain exactly which twin work products the gate requires."""
        if not phase.required_deliverables:
            return ""
        pid = context.project_id or "<the project>"
        hints = deliverable_hints(pid)
        lines = "\n".join(
            f"  - {d}: {hints.get(d, 'record it into the twin, scoped to the project')}"
            for d in phase.required_deliverables
        )
        return (
            "This gate will FAIL unless the following work products are actually "
            f"recorded into the twin for this project during this phase:\n{lines}\n"
        )

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        prompt = self._prompt(goal, phase, context)
        budget = phase_step_budget(phase, self._max_steps)
        logger.info(
            "design_flow_brain_phase",
            phase=phase.id,
            project_id=context.project_id,
            max_steps=budget,
            heavy=phase_is_heavy(phase),
        )
        slots = effective_slots(phase)
        with phase_slot_scope(slots, context.project_id):
            summary = await self._turn(prompt, phase, budget)
        # run_chat_turn returns a fallback sentence when the loop doesn't converge.
        status = "exhausted" if summary.startswith("I couldn't converge") else "completed"
        if status == "exhausted":
            summary = f"{summary} (phase '{phase.id}' used its full budget of {budget} steps.)"
            logger.warning("design_flow_phase_exhausted", phase=phase.id, max_steps=budget)
        await self._backstop_decision(phase, context, summary)
        return PhaseOutcome(summary=summary, artifacts=[], status=status)

    async def _turn(self, prompt: str, phase: Phase, budget: int) -> str:
        return await run_chat_turn(
            prompt,
            mcp_bridge=self._bridge,
            session_id=f"{self._session_id}:{phase.id}",
            max_steps=budget,
            provider=self._provider,
            model=self._model,
            metrics=get_metrics(),
            # MET-747 follow-up: scope the registered MCP tools to this
            # phase's disciplines (plus the always-visible core adapters) --
            # the direct fix for the OpenAI 128-tool-array cap as the tool
            # catalog keeps growing; search_tools is the mid-turn escape
            # hatch if a phase genuinely needs a tool outside its scope.
            domains=phase.disciplines,
            # FORGE-479: an exact, profile-derived tool set (<= 40 with the
            # harness's own tools) instead of every core adapter. An empty
            # discipline tuple used to mean "all tools"; it now means the
            # common set, which is what the intent/needs/requirements phases use.
            tool_allowlist=_report_phase_tools(phase),
            # MET-707: this turn is unattended — nothing will ever resolve a
            # requires_approval tool call's /v1/chat/tool_approvals entry for
            # a design-flow-originated run, so chat's 30-minute default
            # (tuned for a human who might approve any time in that window)
            # just stalls every phase that records a decision. Design-flow's
            # own phase-level gate is the real HITL checkpoint here.
            approval_timeout_seconds=design_flow_approval_timeout_seconds(),
            # FORGE-490: no in-process hold; the sidecar's service-caller policy decides.
            approval_mode="forward",
        )

    async def _backstop_decision(self, phase: Phase, context: FlowContext, summary: str) -> None:
        """Guarantee a phase's ``design_decision`` deliverable.

        A native phase sometimes ends without recording a decision (e.g. an
        electronics phase runs ERC checks but never records the design). Record
        the phase summary as a decision so the deliverable can't be silently
        skipped — an extra summary ADR is harmless, and it strengthens the
        digital thread. Best-effort: never fails the phase.
        """
        if "design_decision" not in phase.required_deliverables or self._bridge is None:
            return
        try:
            args: dict[str, str] = {
                "title": f"{phase.title} — phase summary",
                "rationale": summary,
            }
            if context.project_id:
                args["project_id"] = context.project_id
            await self._bridge.invoke("twin.record_decision", args)
            logger.info("phase_decision_backstop_recorded", phase=phase.id)
        except Exception as exc:  # noqa: BLE001 - backstop must never break the phase
            logger.warning("phase_decision_backstop_failed", phase=phase.id, error=str(exc))
