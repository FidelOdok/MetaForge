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

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
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


class ProposeFlowRequest(BaseModel):
    intent: str
    projectId: str | None = None  # noqa: N815
    requirements: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class FlowChangeView(BaseModel):
    op: str
    phase: str
    value: object | None = None
    rationale: str


class FlowProposalView(BaseModel):
    """A tailored flow, awaiting a human.

    ``approvalId`` is the held write. Nothing starts from a proposal: a run
    is created from the *approved* flow, and until somebody answers that
    approval there is nothing to run.
    """

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
        changes=[
            FlowChangeView(
                op=op.kind.value, phase=op.phase_id, value=op.value, rationale=op.rationale
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


@router.post("/propose", response_model=FlowProposalView, status_code=201)
async def propose_flow(body: ProposeFlowRequest, request: Request) -> FlowProposalView:
    """Tailor a template to a project's intent, and hold it for a human.

    The response carries an ``approvalId``, not a run. FORGE-398's rule is
    that nothing starts before approval, and the way to make that true is for
    the endpoint that generates a flow to have no ability to start one.
    """
    from api_gateway.chat.tool_approvals import get_approval_store
    from api_gateway.design_flows.generate import (
        GeneratorUnavailableError,
        TailoringRequest,
        generate_proposal,
    )

    if not body.intent.strip():
        raise HTTPException(status_code=400, detail="intent is required")

    try:
        proposal = await generate_proposal(
            TailoringRequest(
                intent=body.intent.strip(),
                project_id=body.projectId,
                requirements=body.requirements,
                provider=body.provider,
                model=body.model,
            )
        )
    except GeneratorUnavailableError as exc:
        # 503 rather than a default template: a flow the human believes was
        # tailored, and was not, is worse than being told it is down.
        logger.error("flow_proposal_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    version_store = get_version_store()
    try:
        version = version_store.save(
            proposal.definition,
            base_template_id=proposal.base_template_id,
            base_version=proposal.base_version,
            changes=proposal.diff(),
            origin="generated",
            intent=proposal.intent,
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
    )
    version.approval_id = run.id
    return _proposal_view(proposal, run.id, version.id)


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
    version.approval_id = run.id
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
