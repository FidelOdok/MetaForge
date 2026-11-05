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
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from api_gateway.chat.harness_backend import design_flow_approval_timeout_seconds, run_chat_turn
from api_gateway.chat.routes import get_metrics
from mcp_core.context import ItemSlotClaim, McpCallContext, current_context, with_context
from mcp_core.profiles import phase_overflow, tools_for_phase, unmapped_disciplines
from orchestrator.design_flow.context import INTENT_PHASE_ID
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.prior_lessons import PriorLesson, render_lessons
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

#: ``project_id -> DecidingAnswers | None``: the answers asked once at intent.
AnswersReader = Callable[[str | None], Awaitable[Any]]
#: ``project_id -> lessons``: what earlier runs on the project learned.
LessonsReader = Callable[[str | None], Awaitable[list[PriorLesson]]]
#: The concept phase: its gate is where a direction is picked, and it is
#: handed the sketch readings a person confirmed (FORGE-762).
CONCEPT_PHASE_ID = "concept_selection"
#: Phases briefed with earlier runs' lessons.
LESSON_PHASES = frozenset({INTENT_PHASE_ID, CONCEPT_PHASE_ID})

#: Disciplines already reported as having no tool profile, so the debug line
#: appears once per process rather than once per phase.
_UNMAPPED_LOGGED: set[str] = set()


def _phase_deliverables(phase: Phase) -> tuple[str, ...]:
    """Every deliverable type the phase must or is expected to produce (FORGE-497)."""
    return tuple(dict.fromkeys([*phase.required_deliverables, *phase.expected_artifacts]))


def _report_phase_tools(phase: Phase) -> frozenset[str]:
    """The phase's tool allowlist, with anything dropped or unmapped made visible."""
    deliverables = _phase_deliverables(phase)
    # FORGE-672: a composed task's own tools are part of its allowlist.
    required = tuple(getattr(phase, "capabilities", ()) or ())
    dropped = phase_overflow(phase.disciplines, deliverables, required)
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
    return tools_for_phase(phase.disciplines, deliverables, required)


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
        "design_sketch": (
            # FORGE-771: the block layout is drawn by the renderer, then shown
            # as an AI concept image made from it.
            "draw the block layout with the render-concept-sketch tool (every part with its "
            "size and position, every joint, each tagged intended, assumption or open), then "
            "make an AI concept image from that sketch with the render-concept-image tool "
            "(layout_node_id = the sketch, a short visual_brief); a sketch or image shows "
            "intent and never claims a measured, verified or manufacturable result, "
            f"project_id={pid}"
        ),
        "render": (
            "in the concept phase, make it from the concept's sketch with the "
            "render-concept-image tool (layout_node_id = the sketch's node id, a short "
            "visual_brief: product, materials, finish, style); after CAD, render the "
            f"committed model with the render-work-product tool, project_id={pid}"
        ),
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
            "NOT count. Pass material=<what the part is made of, the material chosen at "
            "the route gate> on EVERY commit, each part and the assembly: a cad_model "
            "with no material fails the gate's material requirement. If the design has "
            "MORE THAN ONE part, commit each part as its own "
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
            "record the pin map file (CSV or JSON) with the record-design-files tool "
            "(work_product_type='pinmap', file_path, name, source_node_ids=[the schematic's "
            "node id]), or derive it from joints with the create-firmware-scaffold tool "
            f"(work_product_id=a work product with metadata.assembly.joints), project_id={pid}"
        ),
        "firmware_source": (
            "after firmware.build, record the PlatformIO project folder with the "
            "record-design-files tool (work_product_type='firmware_source', file_path=the "
            "project_dir, name, source_node_ids=[the pinmap's node id], metadata={the build's "
            "flash and ram}); its build output is kept with it. From joints instead: the "
            f"create-firmware-scaffold tool, project_id={pid}"
        ),
        "schematic": (
            "generate it with kicad.generate_schematic, then record the project folder with "
            "the record-design-files tool (work_product_type='schematic', file_path=the "
            "schematic_file, name, source_node_ids=[the bom's node id], metadata={erc errors "
            f"and warnings}}), project_id={pid}"
        ),
        "pcb_layout": (
            "generate and route it (kicad.generate_pcb, kicad.autoroute_pcb), then record "
            "the routed board with the record-design-files tool "
            "(work_product_type='pcb_layout', file_path=the routed pcb_file, name, "
            "source_node_ids=[the schematic's node id], metadata={drc errors, unrouted}), "
            f"project_id={pid}"
        ),
        "gerber": (
            "export with kicad.export_gerber and kicad.export_drill into one folder, then "
            "record it with the record-design-files tool (work_product_type='gerber', "
            "file_path=that folder, name, source_node_ids=[the pcb_layout's node id]), "
            f"project_id={pid}"
        ),
        "pick_and_place": (
            "export with kicad.export_pos, then record the file with the record-design-files "
            "tool (work_product_type='pick_and_place', file_path, name, "
            f"source_node_ids=[the pcb_layout's node id]), project_id={pid}"
        ),
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
UNATTENDED_PHASE_RULE = (
    "This phase runs unattended: no person reads your reply until the gate, and "
    "nothing can answer a question while the phase runs. Where the objective or a "
    "procedure in this prompt says to ask the person, do NOT ask and stop. Record "
    "what you have instead: where the objective allows a deferred decision (for example a "
    "manufacturing_route with deferred: true), record it with your open questions as "
    "its reason; otherwise decide with the information you have and state each "
    "assumption in the rationale. The reviewer answers at the gate, and a retry "
    "brings their answer back to you. A reply that only asks questions records "
    "nothing, and the gate will not pass."
)
"""What a server-run phase does instead of asking the person (FORGE-714).

Skills and phase objectives say "ask the person for anything missing", which is
right for an interactive client. A phase this brain runs has no one to answer:
on a chair brief with no manufacturing context, route_selection asked for the
quantity and materials, stopped after two steps having recorded nothing, asked
again on every retry, and the run failed when the findings stopped changing.
Client-mode runs do not use this prompt, so a connected client still asks."""

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


async def _manufacturing_input(context: FlowContext) -> str:
    """The project's manufacturing context block, read now rather than frozen (DFM-001).

    The frozen flow context says what the requester stated at proposal; this
    is the shared record as it stands for this phase, with its revision, so a
    route the person changed in Create, or a decision approved at an earlier
    gate, reaches the next phase. Never raises.
    """
    if not context.project_id:
        return ""
    try:
        from api_gateway.twin.manufacturing_context import manufacturing_context_block

        block = await manufacturing_context_block(
            context.project_id, generator="flow_phase", hint=context.goal
        )
    except Exception as exc:  # noqa: BLE001 - the phase runs on its brief without it
        logger.warning("flow_manufacturing_context_unreadable", error=str(exc))
        return ""
    return f"{block.text}\nNever present an assumption above as the person's choice.\n"


async def _phase_inputs(phase: Phase, context: FlowContext) -> str:
    """Person-confirmed inputs for this phase, as prompt text.

    Every phase gets the project's manufacturing context (DFM-001). The
    concept phase also gets the sketch readings a person confirmed; a
    proposed or rejected reading is never offered. A twin that cannot be
    read leaves the phase to run on its brief, logged, rather than failing it.
    """
    manufacturing = await _manufacturing_input(context)
    if phase.id != CONCEPT_PHASE_ID or not context.project_id:
        return manufacturing
    return manufacturing + await _sketch_inputs(context)


async def _sketch_inputs(context: FlowContext) -> str:
    """The concept phase's confirmed sketch readings, as prompt text."""
    if not context.project_id:
        return ""
    try:
        from api_gateway.twin.routes import get_twin
        from api_gateway.twin.sketch_reading import (
            concept_inputs_block,
            confirmed_sketch_readings,
        )

        readings = await confirmed_sketch_readings(get_twin(), context.project_id)
    except Exception as exc:  # noqa: BLE001 - an input we cannot read is skipped, not fatal
        logger.warning("concept_sketch_inputs_unreadable", error=str(exc))
        return ""
    if readings:
        logger.info("concept_sketch_inputs", project_id=context.project_id, readings=len(readings))
    return concept_inputs_block(readings)


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
        answers_reader: AnswersReader | None = None,
        lessons_reader: LessonsReader | None = None,
    ) -> None:
        self._bridge = mcp_bridge
        self._session_id = session_id
        self._answers_reader = answers_reader
        self._lessons_reader = lessons_reader
        self._max_steps = max_steps
        self._provider = provider
        self._model = model
        # Load the SKILL.md corpus once; each phase injects the procedures for
        # its disciplines (the "select" pillar — procedural context).
        self._cards: list[SkillCard] = load_skill_cards()

    async def _answers_blocks(self, phase: Phase, context: FlowContext) -> str:
        """The deciding answers and earlier runs' lessons, for the phases that need them.

        FORGE-766: every phase after intent gets the deciding answers, never
        asked again. The intent and concept phases also get the lessons of
        earlier runs on this project. Each read is best-effort: a failure
        costs the block, never the phase.
        """
        blocks: list[str] = []
        answers = None
        if phase.id != INTENT_PHASE_ID and self._answers_reader is not None:
            try:
                answers = await self._answers_reader(context.project_id)
            except Exception as exc:  # noqa: BLE001 - the brief goes on without it
                logger.warning("design_flow_answers_read_failed", phase=phase.id, error=str(exc))
        if answers is not None:
            blocks.append(answers.brief(include_lessons=phase.id not in LESSON_PHASES))
        if phase.id in LESSON_PHASES and self._lessons_reader is not None:
            try:
                lessons = await self._lessons_reader(context.project_id)
            except Exception as exc:  # noqa: BLE001 - the brief goes on without it
                logger.warning("design_flow_lessons_read_failed", phase=phase.id, error=str(exc))
                lessons = []
            heading = (
                "Lessons from earlier runs on this project (gate rejections, reviewer "
                "findings and recorded lessons). Do not repeat these mistakes"
                + (
                    "; carry the ones that bear on this design into the deciding answers' "
                    "lessons, with their source run:"
                    if phase.id == INTENT_PHASE_ID
                    else "; each concept says how it avoids the ones that apply:"
                )
            )
            block = render_lessons(lessons, heading)
            if block:
                blocks.append(block)
            logger.info("design_flow_prior_lessons", phase=phase.id, lessons=len(lessons))
        return "".join(f"{b}\n\n" for b in blocks)

    def _prompt(
        self,
        goal: str,
        phase: Phase,
        context: FlowContext,
        phase_inputs: str = "",
        answers_block: str = "",
    ) -> str:
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
        # FORGE-762: person-confirmed inputs, such as confirmed sketch readings.
        inputs_block = f"{phase_inputs}\n" if phase_inputs else ""
        return (
            f"{retry_block}"
            f"{flow_block}"
            f"You are MetaForge's autonomous design engineer executing the "
            f"**{phase.title}** phase of a gated design flow.\n\n"
            f"Product goal: {goal}\n"
            f"{project_line}\n\n"
            f"Prior phases completed:\n{prior}\n\n"
            f"{answers_block}"
            f"Your objective for THIS phase:\n{phase.objective}\n\n"
            f"{inputs_block}"
            f"{deliverables}\n"
            f"{items_block}"
            f"{overlay_block}"
            f"{UNATTENDED_PHASE_RULE}\n\n"
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
        phase_inputs = await _phase_inputs(phase, context)
        prompt = self._prompt(
            goal,
            phase,
            context,
            phase_inputs,
            answers_block=await self._answers_blocks(phase, context),
        )
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
        """Keep the phase summary on the run record, never as a decision (FORGE-527).

        This used to record ``"<phase> - phase summary"`` as a
        ``design_decision`` whenever a phase required one, which filled the
        twin with decisions nobody made (14 of 32 in one live project) and let
        a phase pass its decision deliverable without deciding anything. The
        summary already lives on the run record: it is this phase's
        ``PhaseOutcome.summary``, stored by both engines with the phase
        (``completed[].summary``, shown as ``phases[].summary`` by the run
        status). So nothing is written here; a phase that owes a decision and
        records none now shows it as a missing deliverable at its gate.
        Existing phase-summary decisions are left in place (FORGE-529
        migrates them).
        """
        if "design_decision" not in phase.required_deliverables:
            return
        logger.info(
            "phase_summary_kept_on_run",
            phase=phase.id,
            project_id=context.project_id,
            summary_chars=len(summary),
            note="no design_decision is recorded from the phase summary",
        )
