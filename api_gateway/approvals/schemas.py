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
]
DecisionName = Literal["approve", "reject", "retry", "rework"]
#: ``chat``: a person answered in the MCP client's own prompt (elicitation),
#: recorded by the sidecar that asked (FORGE-582).
Surface = Literal["dashboard", "cli", "agent", "chat", "unknown"]

#: Id prefixes (``<prefix>:<native id>``) are coarser than ``kind``: a held
#: tool call, a flow proposal and a human-authority call all live in the tool
#: ledger and are all ``tool:<id>``.
ID_PREFIXES: tuple[str, ...] = ("gate", "tool", "change", "design_loop", "sketch", "drawing")

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
