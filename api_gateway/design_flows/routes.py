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
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FLOWS, flow_version
from orchestrator.design_flow.templates import load_templates

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
    baseTemplateId: str  # noqa: N815
    baseVersion: str  # noqa: N815
    intent: str
    flow: DesignFlowView
    changes: list[FlowChangeView] = Field(default_factory=list)
    valid: bool
    violations: list[str] = Field(default_factory=list)


def _proposal_view(proposal: FlowProposal, approval_id: str) -> FlowProposalView:
    definition = proposal.definition
    return FlowProposalView(
        approvalId=approval_id,
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

    store = get_approval_store()
    run = store.create(
        {
            "kind": "design_flow_proposal",
            "template": proposal.base_template_id,
            "version": proposal.base_version,
            "intent": proposal.intent,
            "changes": proposal.diff(),
            "project_id": body.projectId,
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
    return _proposal_view(proposal, run.id)
