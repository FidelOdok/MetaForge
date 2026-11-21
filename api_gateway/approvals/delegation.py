"""May an agent decide a person's step on this project? Three layers (FORGE-904).

An agent may decide a human-authority step (Imagine accept, handover, rework, an
upload) or a release step for a person only where every layer allows it:

1. **The deployment**: ``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY`` and
   ``METAFORGE_ALLOW_AGENT_RELEASE`` (the operator; off by default).
2. **The account**: its settings can turn either off for every project.
3. **The project**: its policy can turn either off for that project.

A lower layer can only narrow the one above: nothing turns on what the
deployment has off. The answer names the layer that decided, so a refusal says
where to change it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.approvals.delegation")

Kind = Literal["human_authority", "release"]

_ENV = {
    "human_authority": "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY",
    "release": "METAFORGE_ALLOW_AGENT_RELEASE",
}
_WHAT = {
    "human_authority": "decide human-authority steps (accept, hand over, rework, uploads)",
    "release": "fix the release baseline and approve the manufacturing release",
}


@dataclass(frozen=True)
class Delegation:
    allowed: bool
    #: ``deployment``, ``account`` or ``project``: the layer that decided.
    decided_by: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "decided_by": self.decided_by, "reason": self.reason}


def deployment_allows(kind: Kind) -> bool:
    from mcp_core.guardrails import human_authority_delegated, release_delegated

    return release_delegated() if kind == "release" else human_authority_delegated()


async def delegation_allowed(kind: Kind, project_id: str | None) -> Delegation:
    """Whether agents may ``kind`` for a person on ``project_id``, and which layer said so."""
    from api_gateway.approvals.policy import (
        account_for,
        get_account_policy_store,
        get_policy_store,
    )

    field = f"allow_{kind}"
    what = _WHAT[kind]
    with tracer.start_as_current_span("approvals.delegation") as span:
        span.set_attribute("delegation.kind", kind)
        if not deployment_allows(kind):
            return Delegation(
                False,
                "deployment",
                f"this gateway does not let agents {what} ({_ENV[kind]} is off)",
            )
        policy = await get_policy_store().get(project_id) if project_id else None
        account = account_for(policy)
        acct = await get_account_policy_store().get(account) if account is not None else None
        if acct is not None:
            if getattr(acct, field) is False:
                return Delegation(
                    False, "account", f"your account's approval settings do not let agents {what}"
                )
        if policy is not None and getattr(policy, field) is False:
            return Delegation(
                False, "project", f"this project's approval policy does not let agents {what}"
            )
        # The most specific layer that said yes itself; otherwise the deployment's.
        decided_by = (
            "project"
            if policy is not None and getattr(policy, field) is True
            else ("account" if acct is not None and getattr(acct, field) is True else "deployment")
        )
        span.set_attribute("delegation.decided_by", decided_by)
        return Delegation(True, decided_by, f"agents may {what} here")


async def delegation_view(project_id: str | None) -> dict[str, dict[str, object]]:
    """Both delegations for a project, for the dashboard."""
    kinds: tuple[Kind, ...] = ("human_authority", "release")
    return {kind: (await delegation_allowed(kind, project_id)).as_dict() for kind in kinds}
