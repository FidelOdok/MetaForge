"""Serve the flow catalogue the gateway actually runs (FORGE-395).

``GET /v1/design-flows`` did not exist, so
``dashboard/src/api/endpoints/design-flows.ts`` hand-copied the flows out of
``spec.py`` under a comment reading *"Keep this list in sync when a flow is
added or re-phased."* It was not in sync, and nothing could have told anyone:

* ``design_v1`` — the **default** flow — was missing entirely, so the one
  flow a run gets when none is named could not be chosen in the wizard.
* four phase titles were paraphrased (``Electronics`` for *Electronics
  Design*, ``Manufacturing preparation`` for *Manufacturing Prep*, and two
  case differences), so the wizard described phases by names the run never
  uses.

A copy kept in sync by a comment is a copy that drifts. This serves the real
definitions, including the parts the old copy had no way to carry at all —
gate criteria, required deliverables and disciplines — which the New Run
wizard needs to say what a flow will actually demand.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import AliasChoices, BaseModel, Field

from api_gateway.design_flows.lifecycle_service import capability_report
from observability.tracing import get_tracer
from orchestrator.design_flow.context import (
    ClarifyingQuestion,
    FlowContext,
    ManufacturingContext,
    ManufacturingRoute,
    TargetMaturity,
    missing_inputs,
)
from orchestrator.design_flow.generator import FlowProposal, TailoringError
from orchestrator.design_flow.intent import compile_intent
from orchestrator.design_flow.invariants import FlowInvariantError, validate_flow
from orchestrator.design_flow.slots import effective_slots, is_derived
from orchestrator.design_flow.spec import (
    DEFAULT_FLOW_ID,
    FLOWS,
    DeliverableSlot,
    FlowDefinition,
    Gate,
    Phase,
    flow_version,
    get_flow,
)
from orchestrator.design_flow.templates import load_templates
from orchestrator.design_flow.versions import (
    FlowVersion,
    VersionNotFoundError,
    diff_flows,
    get_version_store,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.design_flows.routes")

router = APIRouter(prefix="/v1/design-flows", tags=["design-flows"])


class GateView(BaseModel):
    name: str
    autoApprove: bool = Field(default=False)  # noqa: N815 — dashboard contract is camelCase
    criteria: list[str] = Field(default_factory=list)
    enforceConstraints: bool = Field(default=False)  # noqa: N815
    gateId: str | None = None  # noqa: N815


class SlotView(BaseModel):
    """A deliverable slot: one item the phase writes (FORGE-524)."""

    itemType: str  # noqa: N815
    name: str
    #: The item every write of this deliverable lands on during a run.
    itemKey: str  # noqa: N815
    #: A default slot, derived from the phase at run time and not stored in
    #: the version (so it is not part of its hash).
    derived: bool = False


class PhaseView(BaseModel):
    id: str
    title: str
    objective: str
    expectedArtifacts: list[str] = Field(default_factory=list)  # noqa: N815
    requiredDeliverables: list[str] = Field(default_factory=list)  # noqa: N815
    enforceDeliverables: bool = True  # noqa: N815
    disciplines: list[str] = Field(default_factory=list)
    #: FORGE-477: "provider:model" this phase runs on, or null to route by role.
    model: str | None = None
    gate: GateView | None = None
    #: FORGE-524: the items this phase writes and their keys. Frozen into a
    #: saved version; derived the same way for a template.
    slots: list[SlotView] = Field(default_factory=list)
    #: FORGE-539: the phases this one needs (null = the phase before it), a
    #: run condition over the flow's facts, and the outcome it establishes.
    dependsOn: list[str] | None = None  # noqa: N815
    condition: str | None = None
    outcome: str = ""
    #: Composable flows: set on a task contributed by a domain fragment. The
    #: stage is the spine phase whose gate reviews it; inputs are the items it
    #: reads (``type:name``); checks are its acceptance criteria; capabilities
    #: the tools it needs. Empty on spine phases.
    fragment: str = ""
    stage: str = ""
    inputs: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    #: Set on a block phase: the coupled tasks it solves to convergence.
    coupling: dict[str, Any] | None = None


class FragmentTaskView(BaseModel):
    id: str
    title: str
    stage: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class FragmentBlockView(BaseModel):
    id: str
    stage: str
    members: list[str] = Field(default_factory=list)
    solvesFor: list[str] = Field(default_factory=list)  # noqa: N815
    tolerance: float
    maxIterations: int  # noqa: N815


class FragmentView(BaseModel):
    """A domain fragment: one domain's tasks, composable into any flow."""

    id: str
    version: str
    domain: str
    label: str
    description: str
    spine: str
    requires: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tasks: list[FragmentTaskView] = Field(default_factory=list)
    blocks: list[FragmentBlockView] = Field(default_factory=list)


class FragmentListResponse(BaseModel):
    fragments: list[FragmentView]


def _fragment_view(fragment: Any) -> FragmentView:
    data = fragment.as_dict()
    return FragmentView(
        id=data["id"],
        version=data["version"],
        domain=data["domain"],
        label=data["label"],
        description=data["description"],
        spine=data["spine"],
        requires=data["requires"],
        keywords=data["keywords"],
        tasks=[FragmentTaskView(**t) for t in data["tasks"]],
        blocks=[
            FragmentBlockView(
                id=b["id"],
                stage=b["stage"],
                members=b["members"],
                solvesFor=b["solves_for"],
                tolerance=b["tolerance"],
                maxIterations=b["max_iterations"],
            )
            for b in data["blocks"]
        ],
    )


class DesignFlowView(BaseModel):
    id: str
    #: Full descriptive title, e.g. "Hardware & robotics lifecycle (…)".
    name: str
    #: Short name for a chooser. The dashboard used to hold these itself,
    #: which is why it could drift; they live in the template now.
    label: str
    description: str
    version: str
    isDefault: bool = False  # noqa: N815
    phases: list[PhaseView] = Field(default_factory=list)

    #: Whether this flow passes the server-enforced invariants (FORGE-397).
    #:
    #: Served rather than assumed. A flow that cannot pass its own rules must
    #: not be offered as startable and then refused at ``POST /v1/runs`` —
    #: that reads as the gateway being broken rather than the flow being
    #: wrong, and the person picking it has no way to tell which.
    valid: bool = True
    violations: list[str] = Field(default_factory=list)
    #: FORGE-539: the dependency graph: edges, parallel waves, conditions, and
    #: whether it is the plain straight line. Null when the graph is invalid
    #: (the violations say why).
    graph: dict[str, Any] | None = None


def _graph_dict(definition: Any) -> dict[str, Any] | None:
    from orchestrator.design_flow.graph import ConditionError, GraphError, build_graph

    try:
        return build_graph(definition.phases).as_dict()
    except (GraphError, ConditionError):
        return None


class DesignFlowListResponse(BaseModel):
    flows: list[DesignFlowView]
    defaultFlowId: str  # noqa: N815


def _to_view(flow_id: str) -> DesignFlowView:
    template = load_templates()[flow_id]
    definition = template.definition
    result = validate_flow(definition)
    return DesignFlowView(
        id=definition.id,
        name=definition.name,
        label=template.display_label(),
        description=template.description,
        version=flow_version(flow_id),
        isDefault=flow_id == DEFAULT_FLOW_ID,
        valid=result.ok,
        violations=[str(v) for v in result.violations],
        phases=[_phase_view(phase) for phase in definition.phases],
        graph=_graph_dict(definition),
    )


def _phase_view(phase: object) -> PhaseView:
    gate = getattr(phase, "gate", None)
    return PhaseView(
        id=phase.id,
        title=phase.title,
        objective=phase.objective,
        expectedArtifacts=list(phase.expected_artifacts),
        requiredDeliverables=list(phase.required_deliverables),
        enforceDeliverables=phase.enforce_deliverables,
        disciplines=list(phase.disciplines),
        model=phase.model,
        slots=[
            SlotView(
                itemType=s.item_type,
                name=s.name,
                itemKey=s.item_key,
                derived=is_derived(phase, s),  # type: ignore[arg-type]
            )
            for s in effective_slots(phase)  # type: ignore[arg-type]
        ],
        dependsOn=(
            None if getattr(phase, "depends_on", None) is None else list(phase.depends_on)  # type: ignore[attr-defined]
        ),
        condition=getattr(phase, "condition", None) or None,
        outcome=str(getattr(phase, "outcome", "") or ""),
        fragment=str(getattr(phase, "fragment", "") or ""),
        stage=str(getattr(phase, "stage", "") or ""),
        inputs=list(getattr(phase, "inputs", ()) or ()),
        checks=[c.describe() for c in getattr(phase, "checks", ()) or ()],
        capabilities=list(getattr(phase, "capabilities", ()) or ()),
        coupling=(
            None
            if getattr(phase, "coupling", None) is None
            else {
                "members": list(phase.coupling.members),  # type: ignore[attr-defined]
                "solves_for": list(phase.coupling.solves_for),  # type: ignore[attr-defined]
                "tolerance": phase.coupling.tolerance,  # type: ignore[attr-defined]
                "max_iterations": phase.coupling.max_iterations,  # type: ignore[attr-defined]
            }
        ),
        gate=(
            None
            if gate is None
            else GateView(
                name=gate.name,
                autoApprove=gate.auto_approve,
                criteria=list(gate.criteria),
                enforceConstraints=gate.enforce_constraints,
                gateId=gate.gate_id,
            )
        ),
    )


@router.get("", response_model=DesignFlowListResponse)
def list_design_flows() -> DesignFlowListResponse:
    """Every launchable flow, as the gateway will actually run it."""
    with tracer.start_as_current_span("design_flows.list") as span:
        flows = [_to_view(flow_id) for flow_id in sorted(FLOWS)]
        span.set_attribute("design_flows.count", len(flows))
    logger.info(
        "design_flows_listed",
        count=len(flows),
        invalid=[f.id for f in flows if not f.valid],
    )
    return DesignFlowListResponse(flows=flows, defaultFlowId=DEFAULT_FLOW_ID)


@router.get("/fragments", response_model=FragmentListResponse)
def list_fragments() -> FragmentListResponse:
    """Every domain fragment a proposal can compose in with ``add_fragment``.

    Registered before ``/{flow_id}`` so it is not read as a flow id.
    """
    from orchestrator.design_flow.fragments import load_fragments

    with tracer.start_as_current_span("design_flows.fragments") as span:
        fragments = [_fragment_view(f) for _, f in sorted(load_fragments().items())]
        span.set_attribute("design_flows.fragment_count", len(fragments))
    logger.info("design_flow_fragments_listed", count=len(fragments))
    return FragmentListResponse(fragments=fragments)


@router.get("/{flow_id}", response_model=DesignFlowView)
def get_design_flow(flow_id: str) -> DesignFlowView:
    """One flow, for the run detail view and the plan canvas."""
    if flow_id not in FLOWS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown flow '{flow_id}'; known flows: {sorted(FLOWS)}",
        )
    return _to_view(flow_id)


# ── Tailoring a template to a project (FORGE-398) ────────────────────────


def _alias(camel: str, snake: str) -> AliasChoices:
    # The REST contract is camelCase; snake_case is accepted too because the
    # MCP tool and most hand-written curl calls spell it that way.
    return AliasChoices(camel, snake)


class ManufacturingContextBody(BaseModel):
    """What the project can actually be made with (FORGE-463)."""

    route: ManufacturingRoute | None = None
    processes: list[str] = Field(default_factory=list)
    #: Free-form capability descriptions ("Prusa MK4, 250x210x220 mm").
    machines: list[str] = Field(default_factory=list)
    stockMaterials: list[str] = Field(  # noqa: N815
        default_factory=list, validation_alias=_alias("stockMaterials", "stock_materials")
    )
    productionQuantity: int | None = Field(  # noqa: N815
        default=None,
        ge=1,
        validation_alias=_alias("productionQuantity", "production_quantity"),
    )
    #: FORGE-451: machine-library instance ids chosen for this project.
    machineIds: list[str] = Field(  # noqa: N815
        default_factory=list, validation_alias=_alias("machineIds", "machine_ids")
    )
    #: FORGE-451 / FORGE-446: process and material library entries (ids or names).
    processIds: list[str] = Field(  # noqa: N815
        default_factory=list, validation_alias=_alias("processIds", "process_ids")
    )
    materialIds: list[str] = Field(  # noqa: N815
        default_factory=list, validation_alias=_alias("materialIds", "material_ids")
    )


def _chosen_machine_ids(m: ManufacturingContextBody) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in m.machineIds if x.strip()))


def _library_machine_lines(m: ManufacturingContextBody) -> tuple[str, ...]:
    """The library machines a flow is told about (FORGE-451).

    The chosen ones, each of which must be a machine instance in the library
    (an unknown id is refused rather than dropped, so a typo cannot quietly
    leave a run without its machine). With none chosen, every available
    instance, so the generator and the phases know what exists.
    """
    from api_gateway.library.routes import get_machine_library
    from twin_core.library.machines import (
        MachineInstance,
        MachineLibraryError,
        MachineNotFoundError,
        summarize_limits,
    )

    library = get_machine_library()
    chosen = _chosen_machine_ids(m)
    if chosen:
        lines = []
        for machine_id in chosen:
            try:
                lines.append(summarize_limits(library.limits(machine_id)))
            except MachineNotFoundError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"manufacturingContext.machineIds: no machine '{machine_id}' "
                    "in the machine library (library.list_machines lists them)",
                ) from exc
            except MachineLibraryError as exc:
                raise HTTPException(
                    status_code=422, detail=f"manufacturingContext.machineIds: {exc}"
                ) from exc
        return tuple(lines)
    available = [
        inst
        for inst in library.list(layer="instance")
        if isinstance(inst, MachineInstance) and inst.status == "available"
    ]
    return tuple(summarize_limits(library.limits(inst.id)) for inst in available)


#: With nothing chosen or matched, at most this many entries are listed.
_CATALOG_LIST_CAP = 15


def _catalog_context(m: ManufacturingContextBody) -> dict[str, Any]:
    """Process and material lines for a flow (FORGE-451, FORGE-446).

    Chosen entries must exist (an unknown id or name is refused). With none
    chosen: the entries that match what was stated (processes by name, from
    the stated processes and the library machines' processes; materials by
    the stated stock names), otherwise the library's entries up to a cap.
    """
    from api_gateway.library.catalog_routes import get_catalog
    from api_gateway.library.routes import get_machine_library
    from twin_core.library.catalog import (
        CatalogNotFoundError,
        MaterialEntry,
        ProcessEntry,
        summarize_material,
        summarize_process,
    )
    from twin_core.library.machines import MachineInstance, MachineNotFoundError

    catalog = get_catalog()

    def chosen(kind: str, refs: list[str], field: str) -> list[Any]:
        out = []
        for ref in dict.fromkeys(r.strip() for r in refs if r.strip()):
            try:
                out.append(catalog.get(ref, kind))
            except CatalogNotFoundError as exc:
                plural = "processes" if kind == "process" else "materials"
                raise HTTPException(
                    status_code=422,
                    detail=f"manufacturingContext.{field}: no {kind} '{ref}' in the library "
                    f"(library.list_{plural} lists them)",
                ) from exc
        return out

    def capped(entries: list[Any]) -> list[Any]:
        return entries[:_CATALOG_LIST_CAP]

    processes: list[ProcessEntry] = chosen("process", m.processIds, "processIds")
    process_basis = "chosen" if processes else ""
    if not processes:
        wanted = {p.strip().lower() for p in m.processes if p.strip()}
        library = get_machine_library()
        machine_ids = _chosen_machine_ids(m)
        machines: list[Any] = []
        for machine_id in machine_ids:
            try:
                machines.append(library.get(machine_id))
            except MachineNotFoundError:
                continue  # already refused by the machine resolver
        if not machine_ids:
            machines = [
                i
                for i in library.list(layer="instance")
                if isinstance(i, MachineInstance) and i.status == "available"
            ]
        for machine in machines:
            wanted |= {p.strip().lower() for p in machine.processes}
        every = catalog.list("process")
        processes = [p for p in every if p.name in wanted or p.title.lower() in wanted]
        process_basis = "matched" if processes else ""
        if not processes and every:
            processes, process_basis = capped(every), "listed"

    materials: list[MaterialEntry] = chosen("material", m.materialIds, "materialIds")
    material_basis = "chosen" if materials else ""
    if not materials:
        stated = [s.strip().lower() for s in m.stockMaterials if s.strip()]
        every_m = catalog.list("material")

        def matches(entry: MaterialEntry) -> bool:
            names = [entry.name, entry.title.lower(), entry.designation.lower()]
            return any(s and n and (s in n or n in s) for s in stated for n in names)

        materials = [e for e in every_m if matches(e)]
        material_basis = "matched" if materials else ""
        if not materials and every_m:
            materials, material_basis = capped(every_m), "listed"

    return {
        "process_ids": tuple(p.id for p in processes) if process_basis == "chosen" else (),
        "material_ids": tuple(e.id for e in materials) if material_basis == "chosen" else (),
        "library_processes": tuple(summarize_process(p) for p in processes),
        "library_materials": tuple(summarize_material(e) for e in materials),
        "process_basis": process_basis,
        "material_basis": material_basis,
    }


class CallerOperation(BaseModel):
    """One tailoring operation from the caller's model (FORGE-481)."""

    op: str
    phase: str
    #: Empty is refused by the generator with a named reason, not a schema error.
    rationale: str = ""
    value: object | None = None


class CallerBody(BaseModel):
    """Who is proposing: the client and, if known, its model."""

    client: str | None = None
    model: str | None = None


class ProposeFlowRequest(BaseModel):
    intent: str
    projectId: str | None = None  # noqa: N815
    requirements: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    #: The inputs a flow's shape depends on (FORGE-463). If the route, the
    #: target maturity or the loads are missing, the proposal asks for them
    #: (``status: "needs_input"``) instead of guessing.
    manufacturingContext: ManufacturingContextBody | None = Field(  # noqa: N815
        default=None,
        validation_alias=_alias("manufacturingContext", "manufacturing_context"),
    )
    targetMaturity: TargetMaturity | None = Field(  # noqa: N815
        default=None, validation_alias=_alias("targetMaturity", "target_maturity")
    )
    #: Free text. "unknown" is a valid answer -- it keeps verification in.
    loadsAndUse: str | None = Field(  # noqa: N815
        default=None, validation_alias=_alias("loadsAndUse", "loads_and_use")
    )
    budget: str | None = None
    #: Caller-proposed tailoring (FORGE-481). When ``operations`` or
    #: ``template`` is given the server makes no generator model call: it
    #: applies these with the deterministic generator, runs the same
    #: invariants and holds the same single approval.
    template: str | None = None
    operations: list[CallerOperation] | None = None
    #: Who wrote ``operations``, recorded as provenance.
    caller: CallerBody | None = None

    @property
    def caller_proposed(self) -> bool:
        return self.operations is not None or self.template is not None

    def flow_context(self) -> FlowContext:
        m = self.manufacturingContext
        return FlowContext(
            manufacturing=(
                None
                if m is None
                else ManufacturingContext(
                    route=m.route,
                    processes=tuple(x.strip() for x in m.processes if x.strip()),
                    machines=tuple(x.strip() for x in m.machines if x.strip()),
                    stock_materials=tuple(x.strip() for x in m.stockMaterials if x.strip()),
                    production_quantity=m.productionQuantity,
                    machine_ids=tuple(_chosen_machine_ids(m)),
                    library_machines=_library_machine_lines(m),
                    **_catalog_context(m),
                )
            ),
            target_maturity=self.targetMaturity,
            loads_and_use=self.loadsAndUse,
            budget=self.budget,
            requirements=tuple(r.strip() for r in self.requirements if r.strip()),
        )


class QuestionView(BaseModel):
    id: str
    question: str
    #: Why the answer changes the flow.
    why: str
    #: ``choice`` | ``text`` | ``list`` | ``number``.
    answerType: str  # noqa: N815
    options: list[str] = Field(default_factory=list)
    #: The request field the answer belongs in, for resubmitting.
    field: str = ""
    required: bool = True
    #: ``metaforge`` (deterministic) or ``model`` (product-specific extra).
    source: str = "metaforge"


def _question_view(q: ClarifyingQuestion) -> QuestionView:
    return QuestionView(
        id=q.id,
        question=q.question,
        why=q.why,
        answerType=q.answer_type,
        options=list(q.options),
        field=q.field,
        required=q.required,
        source=q.source,
    )


class FlowNeedsInputView(BaseModel):
    """The proposal could not be made without guessing, so it asks instead.

    Carries no flow and no ``approvalId``: nothing was generated, stored or
    held. Answer the questions and resubmit the same request with them.
    """

    status: Literal["needs_input"] = "needs_input"
    intent: str
    #: One grouped list: MetaForge's required questions first, then at most a
    #: few product-specific ones from the model.
    questions: list[QuestionView]
    notes: list[str] = Field(default_factory=list)
    message: str


class FlowChangeView(BaseModel):
    op: str
    phase: str
    value: object | None = None
    rationale: str
    #: The manufacturing capabilities this change was made under (FORGE-463).
    basis: str = ""


class GeneratedByView(BaseModel):
    """The provider and model that actually produced a proposal (FORGE-468)."""

    provider: str
    model: str
    #: ``"<provider>:<model>"`` of the configured primary when a fallback
    #: answered instead. ``None`` when the primary answered.
    fellBackFrom: str | None = None  # noqa: N815


class FlowProposalView(BaseModel):
    """A tailored flow, awaiting a human.

    ``approvalId`` is the held write. Nothing starts from a proposal: a run
    is created from the *approved* flow, and until somebody answers that
    approval there is nothing to run.
    """

    status: Literal["proposed"] = "proposed"
    approvalId: str  # noqa: N815
    #: The stored, immutable flow this approval is about. Without it, an
    #: approved proposal is a decision about something nobody kept.
    versionId: str  # noqa: N815
    baseTemplateId: str  # noqa: N815
    baseVersion: str  # noqa: N815
    intent: str
    flow: DesignFlowView
    changes: list[FlowChangeView] = Field(default_factory=list)
    valid: bool
    violations: list[str] = Field(default_factory=list)
    #: No requirements were recorded when this was proposed (FORGE-463), so
    #: "every requirement is verified" says nothing yet.
    requirementsPending: bool = False  # noqa: N815
    #: What the proposal took as given without being told.
    assumptions: list[str] = Field(default_factory=list)
    #: Product-specific questions that did not block the proposal.
    openQuestions: list[QuestionView] = Field(default_factory=list)  # noqa: N815
    #: Which provider/model produced this tailoring. ``None`` when unknown.
    generatedBy: GeneratedByView | None = None  # noqa: N815
    #: Set instead of ``generatedBy`` when the caller supplied the operations.
    proposedBy: dict[str, str | None] | None = None  # noqa: N815
    #: FORGE-539: the structured intent this proposal was made for (goal,
    #: outcomes, classified constraints, success criteria, unknowns).
    intentModel: dict[str, Any] | None = None  # noqa: N815
    #: FORGE-539: whether the tools exist to do it: per-phase coverage and
    #: the gap register, with a READY / READY_WITH_WARNINGS / BLOCKED status.
    capabilities: dict[str, Any] | None = None
    #: Composable flows: the fragments composed in, the inputs no task writes
    #: (to be recorded with a source before the tasks that read them) and
    #: each task's derived dependencies. Null for a flow with no fragments.
    composition: dict[str, Any] | None = None


def _generated_by(proposal: FlowProposal) -> dict[str, str | None] | None:
    if proposal.generated_by is None:
        return None
    return {
        "provider": proposal.generated_by.provider,
        "model": proposal.generated_by.model,
        "fell_back_from": proposal.generated_by.fell_back_from,
    }


def _proposal_view(proposal: FlowProposal, approval_id: str, version_id: str) -> FlowProposalView:
    definition = proposal.definition
    return FlowProposalView(
        approvalId=approval_id,
        versionId=version_id,
        baseTemplateId=proposal.base_template_id,
        baseVersion=proposal.base_version,
        intent=proposal.intent,
        valid=proposal.valid,
        violations=[str(v) for v in proposal.validation.violations],
        requirementsPending=proposal.requirements_pending,
        assumptions=list(proposal.assumptions),
        openQuestions=[_question_view(q) for q in proposal.open_questions],
        proposedBy=proposal.proposed_by.as_dict() if proposal.proposed_by else None,
        generatedBy=(
            GeneratedByView(
                provider=proposal.generated_by.provider,
                model=proposal.generated_by.model,
                fellBackFrom=proposal.generated_by.fell_back_from,
            )
            if proposal.generated_by is not None
            else None
        ),
        changes=[
            FlowChangeView(
                op=op.kind.value,
                phase=op.phase_id,
                value=op.value,
                rationale=op.rationale,
                basis=op.basis,
            )
            for op in proposal.operations
        ],
        flow=DesignFlowView(
            id=definition.id,
            name=definition.name,
            label=f"{proposal.base_template_id} (tailored)",
            description=proposal.intent,
            version=proposal.base_version,
            isDefault=False,
            valid=proposal.valid,
            violations=[str(v) for v in proposal.validation.violations],
            phases=[_phase_view(phase) for phase in definition.phases],
            graph=_graph_dict(definition),
        ),
        composition=proposal.composition.as_dict() if proposal.composition else None,
    )


@router.post(
    "/propose",
    response_model=FlowProposalView | FlowNeedsInputView,
    status_code=201,
    responses={200: {"model": FlowNeedsInputView, "description": "Inputs missing; nothing held"}},
)
async def propose_flow(
    body: ProposeFlowRequest, request: Request, response: Response
) -> FlowProposalView | FlowNeedsInputView:
    """Tailor a template to a project's intent, and hold it for a human.

    The response carries an ``approvalId``, not a run. The rule is
    that nothing starts before approval, and the way to make that true is for
    the endpoint that generates a flow to have no ability to start one.

    If the manufacturing route, target maturity or loads are missing, the
    answer is ``200`` with ``status: "needs_input"`` and the questions to
    answer -- no flow, no stored version, no held approval (FORGE-463). A
    generator that guesses those produces a flow that reads as tailored and
    is not.
    """
    from api_gateway.chat.tool_approvals import get_approval_store
    from api_gateway.design_flows.generate import (
        CallerTailoringRequest,
        GeneratorUnavailableError,
        TailoringRequest,
        build_caller_proposal,
        generate_proposal,
    )

    if not body.intent.strip():
        raise HTTPException(status_code=400, detail="intent is required")

    context = body.flow_context()
    if body.projectId:
        # FORGE-571: what the project already has current, so generation can
        # reuse it rather than plan it again. None when it cannot be read.
        from dataclasses import replace

        from api_gateway.design_flows.lifecycle_service import current_item_types
        from api_gateway.twin.routes import get_twin

        try:
            twin = get_twin()
        except Exception:  # noqa: BLE001 - no twin: nothing is reused
            twin = None
        context = replace(context, current_items=await current_item_types(twin, body.projectId))
    missing = missing_inputs(context)
    if missing:
        response.status_code = 200
        return await _needs_input(body, missing)

    if body.caller_proposed:
        # FORGE-481: the caller's model already did the tailoring. No
        # generator call; the required questions above still came from us.
        try:
            proposal = build_caller_proposal(
                CallerTailoringRequest(
                    intent=body.intent.strip(),
                    operations=[o.model_dump(exclude_none=True) for o in body.operations or []],
                    template=body.template,
                    requirements=body.requirements,
                    context=context,
                    client=body.caller.client if body.caller else None,
                    model=body.caller.model if body.caller else None,
                )
            )
        except TailoringError as exc:
            logger.warning("flow_caller_proposal_refused", error=str(exc))
            raise HTTPException(status_code=422, detail=f"flow.propose refused: {exc}") from exc
    else:
        try:
            proposal = await generate_proposal(
                TailoringRequest(
                    intent=body.intent.strip(),
                    project_id=body.projectId,
                    requirements=body.requirements,
                    provider=body.provider,
                    model=body.model,
                    context=context,
                )
            )
        except GeneratorUnavailableError as exc:
            # 503 rather than a default template: a flow the human believes was
            # tailored, and was not, is worse than being told it is down.
            logger.error("flow_proposal_unavailable", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not proposal.valid:
        # Checked here, with the context, before anything is stored: the
        # version store re-validates without it and cannot see, for example,
        # that the loads are unknown.
        detail = str(FlowInvariantError(proposal.base_template_id, proposal.validation.violations))
        logger.warning("flow_proposal_invalid", error=detail)
        raise HTTPException(status_code=422, detail=detail)

    version_store = get_version_store()
    try:
        version = version_store.save(
            proposal.definition,
            base_template_id=proposal.base_template_id,
            base_version=proposal.base_version,
            changes=proposal.diff(),
            origin="caller" if proposal.proposed_by else "generated",
            intent=proposal.intent,
            context=context.render_for_phases(),
            # FORGE-539: what phase conditions are evaluated against, frozen
            # with the version so a condition cannot be steered afterwards.
            facts=context.facts(),
        )
    except FlowInvariantError as exc:
        # A proposal that cannot pass the rules is refused rather than stored
        # as something a human could approve. Approving an unstartable flow
        # is a decision that means nothing.
        logger.warning("flow_proposal_invalid", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # FORGE-539: what the proposal is for, and whether it can be done. Both go
    # to the approver: a gap the server already knows about must not be found
    # by the run, hours in, at a gate.
    intent_model = compile_intent(
        proposal.intent or body.intent,
        context=context,
        requirements=list(body.requirements or []),
        questions=list(proposal.open_questions),
        assumptions=list(proposal.assumptions),
        template_deliverables=list(
            dict.fromkeys(d for ph in proposal.definition.phases for d in ph.required_deliverables)
        ),
    )
    capabilities = await capability_report(proposal.definition.phases)

    store = get_approval_store()
    run = store.create(
        {
            "kind": "design_flow_proposal",
            "template": proposal.base_template_id,
            "version": proposal.base_version,
            "intent": proposal.intent,
            "changes": proposal.diff(),
            "project_id": body.projectId,
            "flow_version_id": version.id,
            "manufacturing_route": context.route.value if context.route else None,
            "target_maturity": context.target_maturity.value if context.target_maturity else None,
            "requirements_pending": proposal.requirements_pending,
            "assumptions": proposal.assumptions,
            # FORGE-468: who produced the tailoring the approver is judging.
            "generated_by": _generated_by(proposal),
            # FORGE-481: set when the caller's model wrote the operations.
            "proposed_by": proposal.proposed_by.as_dict() if proposal.proposed_by else None,
            # FORGE-539: readiness and the gaps behind it, for the approver.
            "capability_status": capabilities.status,
            "capability_gaps": [g.as_dict() for g in capabilities.gaps][:20],
        }
    )
    store.start(run.id)
    store.request_approval(
        run.id,
        reason=(
            f"Start a run on a flow tailored from '{proposal.base_template_id}' "
            f"v{proposal.base_version} with {len(proposal.operations)} change(s)."
        ),
    )
    logger.info(
        "flow_proposal_held",
        approval_id=run.id,
        template=proposal.base_template_id,
        changes=len(proposal.operations),
        valid=proposal.valid,
        generated_by=_generated_by(proposal),
    )
    version = version_store.attach_approval(version.id, run.id)
    view = _proposal_view(proposal, run.id, version.id)
    view.intentModel = intent_model.as_dict()
    view.capabilities = capabilities.as_dict()
    return view


async def _needs_input(
    body: ProposeFlowRequest, missing: list[ClarifyingQuestion]
) -> FlowNeedsInputView:
    """Ask instead of guessing. Creates nothing."""
    from api_gateway.design_flows import generate as gen

    notes: list[str] = []
    extra: list[ClarifyingQuestion] = []
    with tracer.start_as_current_span("design_flows.propose.needs_input") as span:
        span.set_attribute("design_flows.missing", ",".join(q.id for q in missing))
        try:
            # A caller-proposed tailoring (FORGE-481) makes no server model
            # call, not even for the optional product-specific questions.
            extra = (
                []
                if body.caller_proposed
                else await gen.suggest_extra_questions(
                    body.intent.strip(), missing, provider=body.provider, model=body.model
                )
            )
        except gen.GeneratorUnavailableError as exc:
            # Said, not swallowed: the required questions stand on their own,
            # but the person should know the product-specific ones are absent
            # rather than assume there were none.
            span.record_exception(exc)
            notes.append(
                "product-specific questions could not be generated (no model reachable); "
                "the questions above are MetaForge's required ones"
            )
    questions = [*missing, *extra]
    logger.info(
        "flow_proposal_needs_input",
        missing=[q.id for q in missing],
        extra=len(extra),
        project_id=body.projectId,
    )
    return FlowNeedsInputView(
        intent=body.intent.strip(),
        questions=[_question_view(q) for q in questions],
        notes=notes,
        message=(
            "No flow was proposed and nothing is held for approval: the flow depends on "
            "answers that were not given. Answer the questions and resubmit the same "
            "request with them filled in."
        ),
    )


# ── Editing a flow (FORGE-399) ───────────────────────────────────────────


class EditGate(BaseModel):
    name: str
    autoApprove: bool = False  # noqa: N815
    criteria: list[str] = Field(default_factory=list)
    enforceConstraints: bool = False  # noqa: N815
    gateId: str | None = None  # noqa: N815


class EditSlot(BaseModel):
    itemType: str  # noqa: N815
    name: str
    itemKey: str = ""  # noqa: N815
    #: Echoed back from a view; a derived slot is not declared, so it is dropped.
    derived: bool = False


class EditPhase(BaseModel):
    id: str
    title: str
    objective: str
    expectedArtifacts: list[str] = Field(default_factory=list)  # noqa: N815
    requiredDeliverables: list[str] = Field(default_factory=list)  # noqa: N815
    enforceDeliverables: bool = True  # noqa: N815
    disciplines: list[str] = Field(default_factory=list)
    model: str | None = None
    gate: EditGate | None = None
    #: FORGE-524: items the phase declares. ``itemKey`` is optional; the
    #: server binds a missing one when the version is saved.
    slots: list[EditSlot] = Field(default_factory=list)
    #: FORGE-539: graph fields, as on :class:`PhaseView`.
    dependsOn: list[str] | None = None  # noqa: N815
    condition: str | None = None
    outcome: str = ""


class EditFlowRequest(BaseModel):
    """A whole edited flow, plus the template it descends from.

    The editor sends the full flow rather than a patch. A patch would need the
    client and server to agree on how to apply it, and a disagreement there is
    a flow that is not what the person on the canvas was looking at when they
    pressed save.
    """

    baseTemplateId: str  # noqa: N815
    phases: list[EditPhase]
    name: str | None = None


class ValidationView(BaseModel):
    valid: bool
    violations: list[str] = Field(default_factory=list)


class FlowVersionView(BaseModel):
    versionId: str  # noqa: N815
    approvalId: str  # noqa: N815
    baseTemplateId: str  # noqa: N815
    baseVersion: str  # noqa: N815
    status: str
    origin: str
    changes: list[str] = Field(default_factory=list)
    flow: DesignFlowView
    # FORGE-515: the frozen flow context (FORGE-491), part of the content hash.
    context: str = ""
    valid: bool
    violations: list[str] = Field(default_factory=list)


def _definition_from(body: EditFlowRequest) -> FlowDefinition:
    return FlowDefinition(
        id=body.baseTemplateId,
        name=body.name or get_flow(body.baseTemplateId).name,
        phases=tuple(
            Phase(
                id=p.id,
                title=p.title,
                objective=p.objective,
                expected_artifacts=tuple(p.expectedArtifacts),
                required_deliverables=tuple(p.requiredDeliverables),
                enforce_deliverables=p.enforceDeliverables,
                disciplines=tuple(p.disciplines),
                model=p.model or None,
                slots=tuple(
                    DeliverableSlot(item_type=s.itemType, name=s.name, item_key=s.itemKey)
                    for s in p.slots
                    if not s.derived
                ),
                depends_on=None if p.dependsOn is None else tuple(p.dependsOn),
                condition=p.condition or None,
                outcome=p.outcome,
                gate=(
                    None
                    if p.gate is None
                    else Gate(
                        name=p.gate.name,
                        auto_approve=p.gate.autoApprove,
                        criteria=tuple(p.gate.criteria),
                        enforce_constraints=p.gate.enforceConstraints,
                        gate_id=p.gate.gateId,
                    )
                ),
            )
            for p in body.phases
        ),
    )


@router.post("/validate", response_model=ValidationView)
def validate_edited_flow(body: EditFlowRequest) -> ValidationView:
    """Check an edit without saving it.

    The editor calls this as the canvas changes, so a person sees a rule break
    while they are looking at the thing that broke it -- rather than at save,
    by which point they have made five more changes and have to work out which
    one the message is about.
    """
    if body.baseTemplateId not in FLOWS:
        raise HTTPException(status_code=404, detail=f"unknown template '{body.baseTemplateId}'")
    result = validate_flow(_definition_from(body))
    return ValidationView(valid=result.ok, violations=[str(v) for v in result.violations])


@router.post("/versions", response_model=FlowVersionView, status_code=201)
def save_edited_flow(body: EditFlowRequest) -> FlowVersionView:
    """Save an edit as a new version, held for approval.

    Never mutates an existing version. A run pins the version it started on,
    so a version changing underneath would make a completed run's provenance
    a lie -- and an approval that can be edited afterwards is not an approval.
    """
    if body.baseTemplateId not in FLOWS:
        raise HTTPException(status_code=404, detail=f"unknown template '{body.baseTemplateId}'")

    from api_gateway.chat.tool_approvals import get_approval_store

    base = get_flow(body.baseTemplateId)
    candidate = _definition_from(body)
    changes = diff_flows(base, candidate)
    if not changes:
        raise HTTPException(
            status_code=400,
            detail=(
                "this flow is identical to its template; there is nothing to approve. "
                "Start a run on the template directly."
            ),
        )

    version_store = get_version_store()
    try:
        version = version_store.save(
            candidate,
            base_template_id=body.baseTemplateId,
            base_version=flow_version(body.baseTemplateId),
            changes=changes,
            origin="edited",
        )
    except FlowInvariantError as exc:
        # An edit that breaks an invariant cannot be saved -- the ticket's own
        # acceptance criterion. 422 rather than 400: the request was
        # well-formed, the flow was not.
        logger.info("flow_edit_rejected", template=body.baseTemplateId, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    approvals = get_approval_store()
    run = approvals.create(
        {
            "kind": "design_flow_version",
            "template": body.baseTemplateId,
            "flow_version_id": version.id,
            "changes": changes,
        }
    )
    approvals.start(run.id)
    approvals.request_approval(
        run.id,
        reason=f"Approve {len(changes)} change(s) to '{body.baseTemplateId}' before running it.",
    )
    version = version_store.attach_approval(version.id, run.id)
    logger.info(
        "flow_version_held", version_id=version.id, approval_id=run.id, changes=len(changes)
    )
    return _version_view(version)


@router.get("/versions/{version_id}", response_model=FlowVersionView)
def get_flow_version(version_id: str) -> FlowVersionView:
    try:
        return _version_view(get_version_store().get(version_id))
    except VersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _version_view(version: FlowVersion) -> FlowVersionView:
    definition = version.definition
    result = validate_flow(definition)
    return FlowVersionView(
        versionId=version.id,
        approvalId=version.approval_id,
        baseTemplateId=version.base_template_id,
        baseVersion=version.base_version,
        status=version.status.value,
        origin=version.origin,
        changes=version.changes,
        context=version.frozen.context,
        valid=result.ok,
        violations=[str(v) for v in result.violations],
        flow=DesignFlowView(
            id=definition.id,
            name=definition.name,
            label=f"{version.base_template_id} ({version.origin})",
            description=version.intent,
            version=version.frozen.version,
            isDefault=False,
            valid=result.ok,
            violations=[str(v) for v in result.violations],
            phases=[_phase_view(phase) for phase in definition.phases],
        ),
    )


# ---------------------------------------------------------------------------
# FORGE-539: intent compilation and capability coverage
# ---------------------------------------------------------------------------


class IntentModelView(BaseModel):
    """The structured intent (see ``orchestrator.design_flow.intent``)."""

    intent: dict[str, Any]
    #: The required questions still unanswered, as ``flow.propose`` would ask.
    missingInputs: list[QuestionView] = Field(default_factory=list)  # noqa: N815


@router.post("/intent", response_model=IntentModelView)
def compile_flow_intent(body: ProposeFlowRequest) -> IntentModelView:
    """Compile an intent into its structured model. Stores nothing, starts nothing.

    Deterministic: no model call, and no value the person did not state. Use
    it before proposing to see what was understood and what is still unknown.
    """
    if not body.intent.strip():
        raise HTTPException(status_code=400, detail="intent is required")
    with tracer.start_as_current_span("design_flows.compile_intent") as span:
        context = body.flow_context()
        missing = missing_inputs(context)
        template = body.template if body.template in FLOWS else None
        model = compile_intent(
            body.intent.strip(),
            context=context,
            requirements=list(body.requirements or []),
            questions=missing,
            template_deliverables=(
                [d for ph in get_flow(template).phases for d in ph.required_deliverables]
                if template
                else []
            ),
        )
        span.set_attribute("intent.goal", model.goal_type.value)
        span.set_attribute("intent.ready_to_plan", model.ready_to_plan)
    logger.info(
        "design_flow_intent_compiled",
        goal=model.goal_type.value,
        constraints=len(model.constraints),
        criteria=len(model.success_criteria),
        blocking_unknowns=len(model.blocking_unknowns),
    )
    return IntentModelView(
        intent=model.as_dict(), missingInputs=[_question_view(q) for q in missing]
    )


class CapabilityView(BaseModel):
    """Per-phase tool coverage and the gap register for one flow."""

    flowId: str  # noqa: N815
    versionId: str | None = None  # noqa: N815
    profile: str | None = None
    report: dict[str, Any]


def _served(profile: str | None) -> set[str] | None:
    if not profile:
        return None
    from mcp_core.profiles import UnknownProfileError, tools_for_profile

    try:
        return set(tools_for_profile(profile))
    except UnknownProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{flow_id}/capabilities", response_model=CapabilityView)
async def flow_capabilities(flow_id: str, profile: str | None = None) -> CapabilityView:
    """Can this template be run with the tools that exist and answer right now?

    ``profile`` narrows coverage to the tools a client connected with that
    MCP profile is served.
    """
    if flow_id not in FLOWS:
        raise HTTPException(status_code=404, detail=f"unknown template '{flow_id}'")
    report = await capability_report(get_flow(flow_id).phases, served=_served(profile))
    return CapabilityView(flowId=flow_id, profile=profile, report=report.as_dict())


@router.get("/versions/{version_id}/capabilities", response_model=CapabilityView)
async def version_capabilities(version_id: str, profile: str | None = None) -> CapabilityView:
    """The same assessment for a saved (tailored or edited) flow version."""
    try:
        version = get_version_store().get(version_id)
    except VersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    report = await capability_report(version.definition.phases, served=_served(profile))
    return CapabilityView(
        flowId=version.base_template_id,
        versionId=version_id,
        profile=profile,
        report=report.as_dict(),
    )
