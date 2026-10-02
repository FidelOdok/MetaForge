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

from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import AliasChoices, BaseModel, Field

from observability.tracing import get_tracer
from orchestrator.design_flow.context import (
    ClarifyingQuestion,
    FlowContext,
    ManufacturingContext,
    ManufacturingRoute,
    TargetMaturity,
    missing_inputs,
)
from orchestrator.design_flow.generator import FlowProposal
from orchestrator.design_flow.invariants import FlowInvariantError, validate_flow
from orchestrator.design_flow.spec import (
    DEFAULT_FLOW_ID,
    FLOWS,
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
        ),
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

    The response carries an ``approvalId``, not a run. FORGE-398's rule is
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
        GeneratorUnavailableError,
        TailoringRequest,
        generate_proposal,
    )

    if not body.intent.strip():
        raise HTTPException(status_code=400, detail="intent is required")

    context = body.flow_context()
    missing = missing_inputs(context)
    if missing:
        response.status_code = 200
        return await _needs_input(body, missing)

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
            origin="generated",
            intent=proposal.intent,
            context=context.render_for_phases(),
        )
    except FlowInvariantError as exc:
        # A proposal that cannot pass the rules is refused rather than stored
        # as something a human could approve. Approving an unstartable flow
        # is a decision that means nothing.
        logger.warning("flow_proposal_invalid", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    return _proposal_view(proposal, run.id, version.id)


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
            extra = await gen.suggest_extra_questions(
                body.intent.strip(), missing, provider=body.provider, model=body.model
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
