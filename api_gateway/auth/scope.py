"""Which projects a caller may touch (FORGE-849).

Authentication says *who* is calling. On a gateway shared by several accounts
that is not enough: the caller must also be confined to their own projects.
Open core does not know what an account is, so this is the second half of the
provider seam. A provider that can answer "which projects is this principal
allowed?" implements :class:`ProjectAuthorizer` as well as
:class:`~api_gateway.auth.provider.AuthProvider`, and the gateway then runs in
**project-scoped** mode:

* every request is matched to its route before it runs, and the route's
  :class:`RouteTenancy` decides how the project is found (path, query, body);
* a project outside the caller's scope is answered 404, the same as a project
  that does not exist, so ids cannot be probed;
* a route that names no project, and is not on the short reviewed list of
  account-level or shared routes, is **refused** (403). That is the fail-closed
  default: a route added tomorrow is unavailable on a shared gateway until
  someone decides how it is scoped, rather than silently reading across
  accounts.

A provider that does not implement :class:`ProjectAuthorizer` changes nothing:
an authenticated single-team gateway behaves exactly as before, and a local
gateway (``auth_mode: off``) loads none of this.
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from observability.tracing import get_tracer

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.requests import Request
    from starlette.types import ASGIApp

    from api_gateway.auth.principal import Principal

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.auth.scope")

__all__ = [
    "PROJECT_KEYS",
    "ProjectAuthorizer",
    "ProjectScope",
    "RouteTenancy",
    "ScopeDecision",
    "classify_routes",
    "decide",
    "supports_project_scope",
]

#: Parameter and JSON field names that carry a project id.
PROJECT_KEYS: tuple[str, ...] = ("project_id", "projectId")


@dataclass(frozen=True, slots=True)
class ProjectScope:
    """The projects one principal may access."""

    project_ids: frozenset[str] = field(default_factory=frozenset)
    #: The deployment's own services (a worker calling back into the gateway),
    #: never a person: every project and every route, as without scoping.
    unrestricted: bool = False

    def allows(self, project_id: str | None) -> bool:
        if self.unrestricted:
            return True
        return project_id is not None and project_id in self.project_ids


@runtime_checkable
class ProjectAuthorizer(Protocol):
    """Optional provider capability: confine a principal to its projects."""

    async def project_scope(self, principal: Principal) -> ProjectScope:
        """The projects ``principal`` may read and write.

        Raises :class:`~api_gateway.auth.provider.AuthUnavailable` when the
        answer cannot be reached; that is a 503, never an empty scope that
        looks like "no projects".
        """
        ...

    async def project_created(self, principal: Principal, project_id: str) -> None:
        """Record that ``principal`` created ``project_id`` and may access it.

        Raising undoes the creation: a project nobody can reach is worse than
        a failed request.
        """
        ...


def supports_project_scope(provider: object) -> bool:
    """True when ``provider`` can confine principals to their projects."""
    return callable(getattr(provider, "project_scope", None)) and callable(
        getattr(provider, "project_created", None)
    )


class RouteTenancy(enum.Enum):
    """How a route is confined on a project-scoped gateway."""

    #: ``{project_id}`` in the path.
    PROJECT_PATH = "project_path"
    #: ``project_id`` (or ``projectId``) query parameter, required when scoped.
    PROJECT_QUERY = "project_query"
    #: ``project_id`` (or ``projectId``) in the JSON body, required when scoped.
    PROJECT_BODY = "project_body"
    #: ``GET /v1/projects``: answered, then filtered to the caller's scope.
    ACCOUNT_LIST = "account_list"
    #: ``POST /v1/projects``: the new project is registered to the caller.
    ACCOUNT_CREATE = "account_create"
    #: Reference data shared by every account; reads only.
    SHARED_READ = "shared_read"
    #: Not yet scoped: refused on a project-scoped gateway.
    BLOCKED = "blocked"


#: Routes that name no project but are safe on a shared gateway, by
#: ``(method, path)``. Reviewed by hand; keep it short. Everything else that
#: names no project is BLOCKED.
EXPLICIT: dict[tuple[str, str], RouteTenancy] = {
    ("GET", "/v1/projects"): RouteTenancy.ACCOUNT_LIST,
    ("POST", "/v1/projects"): RouteTenancy.ACCOUNT_CREATE,
}

#: Path prefixes of shared reference data, readable (GET) by any account.
#: Writes under them are BLOCKED: one account must not edit what all see.
SHARED_READ_PREFIXES: tuple[str, ...] = (
    "/v1/library/",
    "/v1/examples",
    "/v1/features",
)


def _body_has_project(route: Any) -> bool:
    for param in getattr(route.dependant, "body_params", []):
        if param.name in PROJECT_KEYS:
            return True
        model = getattr(param.field_info, "annotation", None) or getattr(param, "type_", None)
        fields = getattr(model, "model_fields", None) or {}
        if any(k in fields for k in PROJECT_KEYS):
            return True
        if any(getattr(f, "alias", None) in PROJECT_KEYS for f in fields.values()):
            return True
    return False


def classify_route(method: str, route: Any) -> RouteTenancy:
    """The tenancy of one ``APIRoute`` for one HTTP method."""
    path: str = route.path
    explicit = EXPLICIT.get((method, path))
    if explicit is not None:
        return explicit
    dependant = route.dependant
    if any(p.name in PROJECT_KEYS for p in dependant.path_params):
        return RouteTenancy.PROJECT_PATH
    if any(
        p.name in PROJECT_KEYS or getattr(p.field_info, "alias", None) in PROJECT_KEYS
        for p in dependant.query_params
    ):
        return RouteTenancy.PROJECT_QUERY
    if method not in ("GET", "HEAD") and _body_has_project(route):
        return RouteTenancy.PROJECT_BODY
    if method in ("GET", "HEAD") and (path + "/").startswith(SHARED_READ_PREFIXES):
        return RouteTenancy.SHARED_READ
    return RouteTenancy.BLOCKED


def classify_routes(routes: Iterable[Any]) -> dict[tuple[str, str], RouteTenancy]:
    """``(method, path) -> tenancy`` for every API route in ``routes``."""
    from fastapi.routing import APIRoute

    out: dict[tuple[str, str], RouteTenancy] = {}
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            out[(method, route.path)] = classify_route(method, route)
    return out


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    """What the middleware does with one request."""

    allowed: bool
    status: int = 200
    detail: str = ""
    tenancy: RouteTenancy | None = None
    project_id: str | None = None


_UUIDISH = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _match(app: ASGIApp, request: Request) -> tuple[Any | None, dict[str, Any]]:
    from fastapi.routing import APIRoute
    from starlette.routing import Match

    router = getattr(app, "router", None)
    for route in getattr(router, "routes", ()):
        if not isinstance(route, APIRoute):
            continue
        match, child = route.matches(request.scope)
        if match is Match.FULL:
            return route, child.get("path_params", {})
    return None, {}


def _query_project(request: Request) -> str | None:
    for key in PROJECT_KEYS:
        value = request.query_params.get(key)
        if value:
            return value
    return None


async def _body_project(request: Request) -> str | None:
    raw = await request.body()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in PROJECT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def decide(app: ASGIApp, request: Request, scope: ProjectScope) -> ScopeDecision:
    """Whether ``request`` stays inside ``scope``."""
    if scope.unrestricted:
        return ScopeDecision(allowed=True)
    with tracer.start_as_current_span("auth.project_scope") as span:
        route, path_params = _match(app, request)
        if route is None:
            # No API route matches: let routing answer 404/405 itself.
            return ScopeDecision(allowed=True)
        tenancy = classify_route(request.method, route)
        span.set_attribute("route.tenancy", tenancy.value)

        if tenancy is RouteTenancy.PROJECT_PATH:
            pid = next((str(path_params[k]) for k in PROJECT_KEYS if k in path_params), None)
        elif tenancy is RouteTenancy.PROJECT_QUERY:
            pid = _query_project(request)
        elif tenancy is RouteTenancy.PROJECT_BODY:
            pid = _query_project(request) or await _body_project(request)
        elif tenancy in (RouteTenancy.ACCOUNT_LIST, RouteTenancy.ACCOUNT_CREATE):
            return ScopeDecision(allowed=True, tenancy=tenancy)
        elif tenancy is RouteTenancy.SHARED_READ:
            return ScopeDecision(allowed=True, tenancy=tenancy)
        else:
            return ScopeDecision(
                allowed=False,
                status=403,
                tenancy=tenancy,
                detail=(
                    f"{request.method} {route.path} is not available on a gateway shared "
                    "by several accounts: it is not yet confined to a project."
                ),
            )

        if pid is None:
            return ScopeDecision(
                allowed=False,
                status=400,
                tenancy=tenancy,
                detail="project_id is required on a gateway shared by several accounts.",
            )
        if not _UUIDISH.match(pid) or not scope.allows(pid):
            # 404, not 403: the caller learns nothing about other accounts' ids.
            return ScopeDecision(
                allowed=False, status=404, tenancy=tenancy, detail="Project not found"
            )
        span.set_attribute("project.id", pid)
        return ScopeDecision(allowed=True, tenancy=tenancy, project_id=pid)
