"""Schemas for the unified approvals API (FORGE-507)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ApprovalKind = Literal[
    "gate",
    "flow_proposal",
    "flow_version",
    "flow_patch",  # FORGE-539: a change to a running flow
    "tool_call",
    "human_authority",
    "design_change",
    "design_loop",
    "sketch",
    "drawing",
    "upload",  # files from outside, staged until a person approves them
    "parametric_sketch",  # a sketch promoted from its interpretation
]
DecisionName = Literal["approve", "reject", "retry", "rework"]
#: ``chat``: a person answered in the MCP client's own prompt (elicitation),
#: recorded by the sidecar that asked (FORGE-582).
Surface = Literal["dashboard", "cli", "agent", "chat", "reviewer", "unknown"]

#: Id prefixes (``<prefix>:<native id>``) are coarser than ``kind``: a held
#: tool call, a flow proposal and a human-authority call all live in the tool
#: ledger and are all ``tool:<id>``.
ID_PREFIXES: tuple[str, ...] = (
    "gate",
    "tool",
    "change",
    "design_loop",
    "sketch",
    "drawing",
    "upload",
    "parametric_sketch",
)

#: Decisions that must carry a reason (422 otherwise).
REASON_REQUIRED_FOR: tuple[str, ...] = ("reject", "retry", "rework")


class GateFinding(BaseModel):
    """One thing a gate found, classified so a UI need not parse prose."""

    kind: Literal[
        "missing_deliverable",
        "ungrounded",
        "constraint_violation",
        "analysis",
        "geometry",
        "other",
    ]
    severity: Literal["error", "warning", "info"] = "error"
    message: str


class DecisionRecord(BaseModel):
    """Who decided, and through what (FORGE-507)."""

    decision: str
    reason: str = ""
    approver: str | None = None
    approver_verified: bool = False
    #: ``unknown`` when the header was absent or the decision was taken on a
    #: legacy route that records no surface.
    surface: Surface = "unknown"
    #: For ``surface=agent``: the human the agent says it acts for.
    on_behalf_of: str | None = None
    #: For ``surface=agent``: the agent itself, e.g. ``claude-code`` (FORGE-510).
    agent: str | None = None
    decided_at: str | None = None


class GateReview(BaseModel):
    """A reviewer agent's note on one gate (FORGE-726).

    Written for the gate's phase and attempt, so a review of an earlier
    attempt is never shown against a later one.
    """

    phase: str
    attempt: int | None = None
    gate: str = ""
    #: ``none`` when the reviewer could not reach a view (``error`` says why).
    recommendation: Literal["approve", "retry", "rework", "reject", "none"]
    to_phase: str = ""
    reasons: str = ""
    concerns: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["server", "client"] = "server"
    #: The model that reviewed, ``provider:model`` when the server reviewer
    #: ran (as the provider pipeline recorded it).
    model: str = ""
    #: Where the server reviewer's model came from: ``policy`` (the project's
    #: ``reviewer_model``), ``env`` (``METAFORGE_REVIEWER_MODEL``) or
    #: ``default`` (the gateway's chat model). Empty for a client review.
    model_source: str = ""
    #: ``reviewer:<source>:<model>``; what a decision it makes is recorded as.
    reviewer: str = ""
    #: FORGE-730: a client reviewer from the same client that did the phase.
    #: The server cannot tell a subagent from its parent session, so such a
    #: review is shown as advice and never acted on by the policy.
    same_client: bool = False
    created_at: str | None = None
    error: str | None = None
    #: What the policy did with it: ``advised``, ``decided`` or ``held`` (why in ``applied_note``).
    applied: str | None = None
    applied_note: str | None = None


class ApprovalItem(BaseModel):
    """One normalized approval, whatever it is about."""

    id: str
    kind: ApprovalKind
    status: Literal["pending", "approved", "rejected", "expired", "canceled", "retried", "reworked"]
    title: str
    summary: str = ""
    project_id: str | None = None
    created_at: str | None = None
    deadline: str | None = None
    route: Literal["dashboard", "elicitation"] | None = None
    requested_by: str | None = None
    reason_held: str | None = None
    findings: list[GateFinding] = Field(default_factory=list)
    allowed_decisions: list[DecisionName] = Field(default_factory=list)
    #: For ``rework``: the earlier phase ids that are valid ``to_phase`` values.
    rework_targets: list[str] = Field(default_factory=list)
    reason_required_for: list[str] = Field(default_factory=lambda: list(REASON_REQUIRED_FOR))
    decidable: bool = False
    not_decidable_reason: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    decision: DecisionRecord | None = None
    #: FORGE-726: the reviewer agent's note on this gate, when the project's
    #: approval policy asks for one.
    review: GateReview | None = None
    #: FORGE-785 (RT-030): the revision of the thing this approval is about,
    #: where one is known (a sketch's or drawing's content hash). A decision
    #: that names an older one is refused with 409. ``None``: not tracked.
    base_revision: str | None = None


class ApprovalListResponse(BaseModel):
    items: list[ApprovalItem]
    #: Items left out because they carry no project, when scoped to one.
    unscoped_count: int = 0


class DecisionRequest(BaseModel):
    """Body for ``POST /v1/approvals/{id}/decision``.

    ``reviewer`` and ``approved_by`` are accepted (older clients send them) and
    ignored: the deciding human is the authenticated principal.
    """

    model_config = ConfigDict(extra="allow")

    decision: DecisionName
    reason: str = Field(default="", max_length=2000)
    to_phase: str = Field(default="", max_length=200)
    #: FORGE-785 (RT-030): the item's ``base_revision`` as the decider saw it.
    #: Optional; when given and the item has moved on, the decision is 409.
    base_revision: str | None = Field(default=None, max_length=200)


class InlineDecisionRequest(BaseModel):
    """Body for ``POST /v1/approvals/{id}/inline-decision`` (FORGE-582).

    A person answered a gate in the MCP client's own prompt. The sidecar that
    asked records it here. ``approver`` is who the sidecar's authenticated
    MCP session says answered; it is recorded as verified only when this
    request itself is authenticated, the same rule a held tool call's inline
    answer follows (FORGE-473).
    """

    decision: DecisionName
    reason: str = Field(default="", max_length=2000)
    to_phase: str = Field(default="", max_length=200)
    approver: str | None = Field(default=None, max_length=200)
    approver_verified: bool = False
    #: FORGE-785 (RT-030): as on :class:`DecisionRequest`.
    base_revision: str | None = Field(default=None, max_length=200)
