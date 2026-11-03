"""Per-project approval policy (FORGE-725, FORGE-726).

Who decides a design-flow gate, per project:

* ``off`` (default): a person decides every gate.
* ``advise``: a reviewer agent writes a recommendation onto the gate; a
  person still decides. Every decision then also says whether the person
  agreed with the reviewer, which is the evidence for trusting it further.
* ``auto_clean``: the reviewer's *approve* is recorded on a gate whose checks
  all passed with no findings, when it is confident. Anything else waits.
* ``auto``: the reviewer decides anything decidable (approve, retry, rework,
  never reject) until ``expires_at``. Meant for test runs.

Two more fields say where the reviewer's intelligence comes from:
``reviewer`` (``server``: the design-flow worker with its own model;
``client``: a connected client's reviewer subagent claims a review task;
``client_then_server``: the client first, the server when nobody claims it)
and ``reviewer_model`` (the model the server reviewer uses, and the one a
client reviewer is asked to use). An empty ``reviewer_model`` means
``METAFORGE_REVIEWER_MODEL`` for the server reviewer, then the gateway's own
model (``api_gateway.runs.gate_review.reviewer_model``).

Invariants enforced elsewhere: a failed check never offers approve; sign-offs
and spending actions always wait for a person; a reviewer's decision is
recorded as ``reviewer:<source>:<model>``; changing the policy is a person's
decision (``policy_routes``); and ``METAFORGE_AUTO_APPROVE_MAX`` caps the
highest mode any project may use.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

__all__ = [
    "MODES",
    "ApprovalPolicy",
    "PolicyStore",
    "effective_mode",
    "get_policy_store",
    "max_mode",
    "mode_allowed",
    "reset_policy_store",
]

Mode = Literal["off", "advise", "auto_clean", "auto"]
ReviewerSource = Literal["server", "client", "client_then_server"]

#: Weakest to strongest; the operator cap and the expiry fallback use the order.
MODES: tuple[str, ...] = ("off", "advise", "auto_clean", "auto")
MAX_MODE_ENV = "METAFORGE_AUTO_APPROVE_MAX"
#: Without an operator setting a project may ask for advice, nothing stronger.
DEFAULT_MAX_MODE = "advise"
#: ``auto`` must end; this is its default and its ceiling.
DEFAULT_AUTO_HOURS = 24
MAX_AUTO_HOURS = 168


class ApprovalPolicy(BaseModel):
    """One project's policy, as stored."""

    project_id: str
    mode: Mode = "off"
    reviewer: ReviewerSource = "server"
    reviewer_model: str = ""
    expires_at: str | None = None
    updated_by: str = ""
    updated_at: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def max_mode() -> str:
    """The operator cap, ``METAFORGE_AUTO_APPROVE_MAX`` (default ``advise``)."""
    raw = os.environ.get(MAX_MODE_ENV, "").strip().lower()
    return raw if raw in MODES else DEFAULT_MAX_MODE


def mode_allowed(mode: str) -> bool:
    return mode in MODES and MODES.index(mode) <= MODES.index(max_mode())


def effective_mode(policy: ApprovalPolicy, now: datetime | None = None) -> str:
    """The mode in force now: capped, and ``auto`` past its expiry is ``advise``."""
    mode = policy.mode
    if mode == "auto" and policy.expires_at:
        try:
            ends = datetime.fromisoformat(policy.expires_at)
        except ValueError:
            ends = None
        if ends is None or (now or datetime.now(UTC)) >= ends:
            mode = "advise"
    while not mode_allowed(mode):
        mode = MODES[MODES.index(mode) - 1]
    return mode


def auto_expiry(hours: float | None, now: datetime | None = None) -> str:
    span = min(max(float(hours or DEFAULT_AUTO_HOURS), 0.1), MAX_AUTO_HOURS)
    return ((now or datetime.now(UTC)) + timedelta(hours=span)).isoformat()


class PolicyStore:
    """In-memory policies; the Postgres store below keeps the same interface."""

    def __init__(self) -> None:
        self._policies: dict[str, ApprovalPolicy] = {}

    async def get(self, project_id: str) -> ApprovalPolicy:
        return self._policies.get(project_id) or ApprovalPolicy(project_id=project_id)

    async def put(self, policy: ApprovalPolicy) -> ApprovalPolicy:
        self._policies[policy.project_id] = policy
        return policy


class PgPolicyStore(PolicyStore):
    """``approval_policies`` table. ``create_all`` adds it to an existing database."""

    async def get(self, project_id: str) -> ApprovalPolicy:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import ApprovalPolicyRow

        async with get_session() as session:
            row = await session.get(ApprovalPolicyRow, project_id)
            if row is None:
                return ApprovalPolicy(project_id=project_id)
            return ApprovalPolicy.model_validate(row.policy | {"project_id": project_id})

    async def put(self, policy: ApprovalPolicy) -> ApprovalPolicy:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import ApprovalPolicyRow

        async with get_session() as session:
            await session.merge(
                ApprovalPolicyRow(project_id=policy.project_id, policy=policy.model_dump())
            )
        return policy


_store: PolicyStore | None = None


def get_policy_store() -> PolicyStore:
    """The process's policy store: Postgres when the gateway has a database."""
    global _store  # noqa: PLW0603
    if _store is None:
        try:
            from api_gateway.db import HAS_SQLALCHEMY
            from api_gateway.db.engine import get_engine

            _store = (
                PgPolicyStore() if HAS_SQLALCHEMY and get_engine() is not None else PolicyStore()
            )
        except Exception as exc:  # noqa: BLE001 - no database: keep policies in memory
            logger.warning("approval_policy_store_in_memory", error=str(exc))
            _store = PolicyStore()
    return _store


def reset_policy_store(store: PolicyStore | None = None) -> None:
    global _store  # noqa: PLW0603
    _store = store
