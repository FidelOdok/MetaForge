"""FastAPI dependencies for reading the authenticated caller.

The middleware does the verifying; these just surface the result to a route.

Two dependencies, because routes want different things:

* :func:`current_principal` — "who is calling, if anyone". Returns ``None`` on a
  local gateway, where there is no such concept. Use this for behaviour that
  varies by caller but works fine without one, such as stamping an ``actor_id``.
* :func:`require_principal` — "there must be a caller". Use this for anything
  account-scoped, so the route is honest about not working in local mode rather
  than silently operating on someone else's data.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from api_gateway.auth.principal import Principal

__all__ = ["current_principal", "require_principal"]


def current_principal(request: Request) -> Principal | None:
    """The verified caller, or ``None`` when authentication is off."""
    return getattr(request.state, "principal", None)


def require_principal(request: Request) -> Principal:
    """The verified caller. 401 if there is none.

    Reaching the 401 on a cloud gateway should be impossible — the middleware
    rejects unauthenticated requests before any route runs. It fires when a
    route needing an account is called on a local gateway, and the message says
    so rather than implying a bad credential.
    """
    principal = current_principal(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "This endpoint requires an account. The gateway is running with "
                "METAFORGE_AUTH_MODE=off, which has no concept of one."
            ),
        )
    return principal
