"""Who a decision is attributable to (FORGE-393).

An approval click is a human act whether or not the gateway authenticates.
On a cloud gateway the middleware has already verified a principal and that
is the identity. On a local gateway ``METAFORGE_AUTH_MODE=off`` means there
are no identities to verify — but a person still clicked, and recording that
honestly (``verified=False``) is different in kind from recording a name the
model typed, which records something nobody ever established.

This is the only place a :class:`~mcp_core.guardrails.Approver` is built from
an HTTP request, so there is one answer to "where did that name come from".
"""

from __future__ import annotations

from fastapi import Request

from api_gateway.auth.dependencies import current_principal
from mcp_core.guardrails import Approver

__all__ = ["LOCAL_DASHBOARD_ACTOR", "approver_from_request"]

#: Stands in for the person at an unauthenticated local gateway. Deliberately
#: not a name: it says "a human at this dashboard", which is all that is known.
LOCAL_DASHBOARD_ACTOR = "local:dashboard"


def approver_from_request(request: Request) -> Approver:
    """The human this request is attributable to.

    Never returns ``None``: reaching a decision route at all means somebody
    acted. What varies is whether the identity was verified.
    """
    principal = current_principal(request)
    if principal is not None:
        return Approver(
            actor_id=principal.actor_id,
            verified=True,
            display_name=principal.email,
        )
    return Approver(actor_id=LOCAL_DASHBOARD_ACTOR, verified=False)
