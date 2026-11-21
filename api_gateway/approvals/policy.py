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
    "AccountPolicy",
    "AccountPolicyStore",
    "ApprovalPolicy",
    "PolicyStore",
    "account_for",
    "get_account_policy_store",
    "reset_account_policy_store",
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
    # FORGE-904: may agents decide this project's human-authority steps (Imagine
    # accept, handover, rework, an upload) and its release for a person? ``None``
    # follows the account; ``False`` turns it off here. ``True`` cannot reach past
    # the account or the deployment (see api_gateway.approvals.delegation).
    allow_human_authority: bool | None = None
    allow_release: bool | None = None
    #: The account the project belongs to, recorded when a person saves the policy,
    #: so a caller with no account of its own (a worker) still finds the account's.
    account: str | None = None
    #: The account's ceiling, attached when the policy is read. Never stored.
    account_max_mode: str | None = Field(default=None, exclude=True)


class AccountPolicy(BaseModel):
    """One account's approval settings (FORGE-904): every project of the account.

    ``max_mode`` is the highest mode the account's projects may use, at most the
    deployment's; ``None`` follows the deployment. The ``allow_*`` switches turn
    agent delegation off for every project (``False``) or follow the deployment
    (``None``/``True``); an account cannot turn on what the deployment has off.
    """

    account: str
    max_mode: Mode | None = None
    allow_human_authority: bool | None = None
    allow_release: bool | None = None
    updated_by: str = ""
    updated_at: str | None = None


def max_mode() -> str:
    """The operator cap, ``METAFORGE_AUTO_APPROVE_MAX`` (default ``advise``)."""
    raw = os.environ.get(MAX_MODE_ENV, "").strip().lower()
    return raw if raw in MODES else DEFAULT_MAX_MODE


def mode_allowed(mode: str) -> bool:
    return mode in MODES and MODES.index(mode) <= MODES.index(max_mode())


def effective_mode(policy: ApprovalPolicy, now: datetime | None = None) -> str:
    """The mode in force now: capped, and ``auto`` past its expiry is ``advise``."""
    mode: str = policy.mode
    if mode == "auto" and policy.expires_at:
        try:
            ends = datetime.fromisoformat(policy.expires_at)
        except ValueError:
            ends = None
        if ends is None or (now or datetime.now(UTC)) >= ends:
            mode = "advise"
    while not mode_allowed(mode):
        mode = MODES[MODES.index(mode) - 1]
    # FORGE-904: and never above the account's own ceiling.
    cap = policy.account_max_mode
    if cap is not None and cap in MODES and MODES.index(mode) > MODES.index(cap):
        mode = cap
    return mode


def account_for(policy: ApprovalPolicy | None = None) -> str | None:
    """The account whose settings apply: the caller's, else the project's recorded one.

    ``""`` is a local gateway's single account. ``None``: no account applies (a
    signed-in person with no account, or a service with no project account).
    """
    from twin_core.library.viewer import NO_TENANT, current_viewer

    viewer = current_viewer()
    if viewer and viewer != NO_TENANT:
        return viewer
    if policy is not None and policy.account:
        return policy.account
    return "" if viewer is None else None


async def _with_account(policy: ApprovalPolicy) -> ApprovalPolicy:
    account = account_for(policy)
    if account is None:
        return policy
    try:
        acct = await get_account_policy_store().get(account)
    except Exception as exc:  # noqa: BLE001 - the deployment cap still applies
        logger.warning("account_policy_unavailable", error=str(exc))
        return policy
    if acct.max_mode is not None:
        policy.account_max_mode = acct.max_mode
    return policy


def auto_expiry(hours: float | None, now: datetime | None = None) -> str:
    span = min(max(float(hours or DEFAULT_AUTO_HOURS), 0.1), MAX_AUTO_HOURS)
    return ((now or datetime.now(UTC)) + timedelta(hours=span)).isoformat()


class PolicyStore:
    """In-memory policies; the Postgres store below keeps the same interface."""

    def __init__(self) -> None:
        self._policies: dict[str, ApprovalPolicy] = {}

    async def get(self, project_id: str) -> ApprovalPolicy:
        stored = self._policies.get(project_id)
        policy = stored.model_copy() if stored else ApprovalPolicy(project_id=project_id)
        return await _with_account(policy)

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
            policy = (
                ApprovalPolicy(project_id=project_id)
                if row is None
                else ApprovalPolicy.model_validate(row.policy | {"project_id": project_id})
            )
        return await _with_account(policy)

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


class AccountPolicyStore:
    """In-memory account settings (FORGE-904); the Postgres store keeps the interface."""

    def __init__(self) -> None:
        self._policies: dict[str, AccountPolicy] = {}

    async def get(self, account: str) -> AccountPolicy:
        return self._policies.get(account) or AccountPolicy(account=account)

    async def put(self, policy: AccountPolicy) -> AccountPolicy:
        self._policies[policy.account] = policy
        return policy


class PgAccountPolicyStore(AccountPolicyStore):
    """``account_approval_policies`` table; ``create_all`` adds it."""

    async def get(self, account: str) -> AccountPolicy:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AccountApprovalPolicyRow

        async with get_session() as session:
            row = await session.get(AccountApprovalPolicyRow, account)
            if row is None:
                return AccountPolicy(account=account)
            return AccountPolicy.model_validate(row.policy | {"account": account})

    async def put(self, policy: AccountPolicy) -> AccountPolicy:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AccountApprovalPolicyRow

        async with get_session() as session:
            await session.merge(
                AccountApprovalPolicyRow(account=policy.account, policy=policy.model_dump())
            )
        return policy


_account_store: AccountPolicyStore | None = None


def get_account_policy_store() -> AccountPolicyStore:
    """The process's account settings: Postgres when the gateway has a database."""
    global _account_store  # noqa: PLW0603
    if _account_store is None:
        try:
            from api_gateway.db import HAS_SQLALCHEMY
            from api_gateway.db.engine import get_engine

            _account_store = (
                PgAccountPolicyStore()
                if HAS_SQLALCHEMY and get_engine() is not None
                else AccountPolicyStore()
            )
        except Exception as exc:  # noqa: BLE001 - no database: keep them in memory
            logger.warning("account_policy_store_in_memory", error=str(exc))
            _account_store = AccountPolicyStore()
    return _account_store


def reset_account_policy_store(store: AccountPolicyStore | None = None) -> None:
    global _account_store  # noqa: PLW0603
    _account_store = store
