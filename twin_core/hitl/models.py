"""HITLLevel/ApprovalRequest models (FORGE-53, spec section 24 Human-in-the-
Loop Model).
"""

from __future__ import annotations

from enum import IntEnum
from uuid import UUID

from pydantic import BaseModel, Field


class HITLLevel(IntEnum):
    """Risk-based approval tiers (spec section 24), ordered low to high so a
    Patch with multiple operations classifies at ``max()`` of its parts."""

    AUTONOMOUS = 0  # unit normalization, typo correction, trace generation, ...
    NOTIFY = 1  # low-risk derived metadata, non-critical classification
    REVIEW = 2  # new derived requirement, decomposition, inferred constraint
    EXPLICIT_APPROVAL = 3  # baseline creation, critical change, deletion, major trade-off
    MANDATORY_AUTHORITY = 4  # safety waiver, regulatory exception, release-to-manufacture


class ApprovalRequest(BaseModel):
    """Outcome of ``HITLEngine.required_approval(patch, impact, state)``."""

    patch_id: UUID
    level: HITLLevel
    # Step 11 of the Harness Execution Contract: if required, execution
    # returns PendingHumanApproval(patch, approval) rather than committing.
    required: bool
    # A human should be informed even when not blocking (level >= NOTIFY).
    notify: bool
    reason: str
    # Spec section 63: some safety-critical categories require the approver
    # to be someone other than the patch's author.
    require_independent_approver: bool = False
    # Safety-critical requirements not expressible as an approval level
    # (e.g. "independent evidence required" for verification, "no stale
    # evidence" for release) -- surfaced here rather than silently dropped.
    notes: list[str] = Field(default_factory=list)
