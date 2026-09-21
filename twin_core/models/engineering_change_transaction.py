"""EngineeringChangeTransaction — the mandatory container for a non-trivial
change (FORGE-66, spec section 13, Phase 7 of epic FORGE-35).

Wraps a real :class:`~twin_core.models.patch.Patch` (FORGE-50) with the
lifecycle state the spec's own ``ECT-00281`` example describes -- trigger,
observation, computed impact, and an approval gate -- rather than inventing
a second, parallel "proposed change" shape. See
``twin_core/transactions/ect.py`` for the state-machine functions that
create and advance one of these.

Not to be confused with the older, unrelated ``twin.propose_change`` MCP
tool (MET-548, ``api_gateway/assistant/proposal_recorder.py`` +
``ApprovalWorkflow``): that mechanism files a free-form
``DesignChangeProposal`` with an opaque ``diff: dict`` whose apply-on-
approve executor only implements a ``record_decision`` action -- every
other diff shape silently no-ops even after human approval, a known,
documented gap. This ECT stack is a proper successor for changes that
touch Constraint/EngineeringEntity graph state: its "diff" is a real,
typed ``Patch`` that ``TransactionEngine.commit`` actually knows how to
apply, for every one of the 7 operations FORGE-50 implements.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType
from twin_core.models.patch import Patch


class ECTStatus(StrEnum):
    """Spec section 13's state machine: PROPOSED -> ANALYZING ->
    READY_FOR_REVIEW -> APPROVED | REJECTED -> COMMITTED | ROLLED_BACK."""

    PROPOSED = "proposed"
    ANALYZING = "analyzing"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


ImpactSeverity = Literal["low", "medium", "high"]


class ChangeTrigger(BaseModel):
    """What set this change off -- e.g. ``{type: "simulation_result", ref:
    "SIM-091"}`` or ``{type: "user_request"}``."""

    type: str
    ref: str | None = None


class EngineeringChangeTransaction(NodeBase):
    """A proposed change, carried through its full review lifecycle before
    (and only before) it's allowed to touch the graph for real."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.ENGINEERING_CHANGE_TRANSACTION
    trigger: ChangeTrigger
    observation: str
    patch: Patch
    # Populated by analyze() -- see twin_core/transactions/ect.py's module
    # docstring for exactly what "affected" means today (direct patch
    # targets) versus the full transitive blast radius (FORGE-67).
    affected_objects: list[str] = Field(default_factory=list)
    impact: ImpactSeverity | None = None
    approval_required: bool | None = None
    status: ECTStatus = ECTStatus.PROPOSED
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_by: str | None = None
    decision_reason: str | None = None
    # PatchResult.model_dump(mode="json") once commit() runs -- kept as a
    # plain dict (not a typed field) so this model doesn't import
    # transactions/engine.py, avoiding a models<->transactions import cycle.
    committed_patch_result: dict[str, Any] | None = None
