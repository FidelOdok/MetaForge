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

import structlog
from fastapi import Request

from api_gateway.auth.dependencies import current_principal
from mcp_core.context import REQUESTER_PREFIX
from mcp_core.guardrails import Approver

logger = structlog.get_logger(__name__)

__all__ = [
    "LOCAL_DASHBOARD_ACTOR",
    "SERVICE_ROLE",
    "approver_from_request",
    "is_service_caller",
    "requester_from_request",
]

#: The ``role`` a provider gives the deployment's own services (the MCP
#: sidecar calling with ``METAFORGE_GATEWAY_API_KEY``), never a person.
SERVICE_ROLE = "service"

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


def is_service_caller(request: Request) -> bool:
    """Whether this request is the deployment's own service, not a person.

    The provider marks its service principal with ``role == "service"``; on
    a project-scoped gateway its scope must also be unrestricted. Both are
    required, so a person's token that happened to carry such a role claim
    is still a person on a shared gateway.
    """
    principal = current_principal(request)
    if principal is None or principal.role != SERVICE_ROLE:
        return False
    scope = getattr(request.state, "project_scope", None)
    return scope is None or bool(getattr(scope, "unrestricted", False))


def requester_from_request(request: Request, supplied: str | None) -> tuple[str | None, bool]:
    """Who asked for a held call, and whether that is established.

    * The deployment's service (the MCP sidecar) relays the person its own
      sign-in verified: the supplied value is taken, as verified.
    * A verified person calling directly is recorded as themselves
      (``sub:<subject>``) whatever the body says, so nobody can file an
      approval in someone else's name.
    * Unauthenticated (``METAFORGE_AUTH_MODE=off``): the supplied value is
      kept as a label, never as verified.
    """
    supplied = (supplied or "").strip() or None
    principal = current_principal(request)
    if principal is None:
        return supplied, False
    if is_service_caller(request):
        return supplied, supplied is not None
    own = f"{REQUESTER_PREFIX}{principal.subject}"
    if supplied is not None and supplied not in (own, principal.subject):
        logger.warning(
            "approval_requester_overridden",
            supplied=supplied,
            recorded=own,
            path=request.url.path,
        )
    return own, True
