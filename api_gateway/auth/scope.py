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
* a route that names an entity instead (a twin node, a run, an approval, a
  chat thread, an agent session, an upload) is checked against the project
  that owns it, looked up in :mod:`api_gateway.auth.ownership` (FORGE-872).
  The same check runs on entity ids nested under a project, so
  ``/projects/{mine}/.../{someone_else's_node}`` is refused too;
* a route that names neither, and is not on the short reviewed list of
  account-level, shared or stateless routes, is **refused** (403). That is the fail-closed
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
    "EntityRule",
    "ENTITY_RULES",
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
    #: Who owns the library entries this principal adds (FORGE-876): an
    #: opaque key the provider chooses, such as the caller's account. ``None``
    #: means the caller may read the shared library but add nothing to it.
    tenant: str | None = None

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
    #: Adds to or changes the caller's own library entries (FORGE-876):
    #: allowed when the scope names a tenant; shared entries stay read-only.
    TENANT_LIBRARY = "tenant_library"
    #: Names an entity, not a project; checked against the entity's owner.
    PROJECT_ENTITY = "project_entity"
    #: Pure computation over the request body: reads and stores nothing.
    STATELESS = "stateless"
    #: Not yet scoped: refused on a project-scoped gateway.
    BLOCKED = "blocked"


#: Routes that name no project but are safe on a shared gateway, by
#: ``(method, path)``. Reviewed by hand; keep it short. Everything else that
#: names no project is BLOCKED.
EXPLICIT: dict[tuple[str, str], RouteTenancy] = {
    ("GET", "/v1/projects"): RouteTenancy.ACCOUNT_LIST,
    ("POST", "/v1/projects"): RouteTenancy.ACCOUNT_CREATE,
    # The run request carries its project (``request.project_id``, or the
    # held tool call's ``project`` / ``arguments.project_id``).
    ("POST", "/v1/runs"): RouteTenancy.PROJECT_BODY,
    # Catalogues with no account data in them: flow templates, the model and
    # tool lists, chat channel kinds.
    ("GET", "/v1/design-flows"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/design-flows/fragments"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/design-flows/{flow_id}"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/design-flows/{flow_id}/capabilities"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/harness/providers"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/harness/models"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/harness/tools"): RouteTenancy.SHARED_READ,
    ("GET", "/v1/chat/channels"): RouteTenancy.SHARED_READ,
    # FORGE-876: an account adds and changes its own library entries; the
    # stores refuse changes to shared ones. Import, discovery and machine jobs
    # stay blocked. Reads, the export included, see shared entries and the
    # caller's own: the middleware sets the request's library view.
    **{
        (method, f"/v1/library/{kind}{suffix}"): RouteTenancy.TENANT_LIBRARY
        for kind, ref in (
            ("processes", "ref"),
            ("materials", "ref"),
            ("components", "ref"),
            ("appearances", "ref"),
            ("ergonomic-limits", "ref"),
            ("machines", "machine_id"),
        )
        for method, suffix in (("POST", ""), ("PATCH", f"/{{{ref}}}"))
    },
    # Computed from the request alone; nothing read, nothing stored.
    ("POST", "/v1/design-flows/validate"): RouteTenancy.STATELESS,
    ("POST", "/v1/robot/joint-loads"): RouteTenancy.STATELESS,
    # Content-addressed: the id is the SHA-256 of the uploaded file, so only
    # someone who already holds the file can name its conversion.
    ("POST", "/v1/convert"): RouteTenancy.STATELESS,
    ("GET", "/v1/convert/{file_hash}"): RouteTenancy.STATELESS,
    ("GET", "/v1/convert/{file_hash}/glb"): RouteTenancy.STATELESS,
    ("GET", "/v1/convert/{file_hash}/metadata"): RouteTenancy.STATELESS,
}


@dataclass(frozen=True, slots=True)
class EntityRule:
    """Under ``prefix``, a parameter or JSON field ``key`` names an entity of
    ``kind`` (a key of :data:`api_gateway.auth.ownership.OWNER_LOOKUPS`)."""

    prefix: str
    key: str
    kind: str


#: How entity ids are recognised. A key is looked for in the path, then the
#: query, then the top level of a JSON body.
ENTITY_RULES: tuple[EntityRule, ...] = (
    EntityRule("/v1/", "node_id", "twin_node"),
    EntityRule("/v1/", "work_product_id", "twin_node"),
    EntityRule("/v1/twin/interference-check", "work_product_id_a", "twin_node"),
    EntityRule("/v1/twin/interference-check", "work_product_id_b", "twin_node"),
    EntityRule("/v1/twin/nodes/boolean-cut", "target_node_id", "twin_node"),
    EntityRule("/v1/twin/nodes/boolean-cut", "cutter_node_id", "twin_node"),
    EntityRule("/v1/decisions", "related_to", "twin_node"),
    EntityRule("/v1/runs/", "run_id", "run"),
    EntityRule("/v1/assistant/request/", "run_id", "run"),
    EntityRule("/v1/chat/tool_approvals/", "run_id", "run"),
    EntityRule("/v1/approvals/", "approval_id", "approval"),
    EntityRule("/v1/chat/threads/", "thread_id", "chat_thread"),
    EntityRule("/v1/sessions/", "session_id", "agent_session"),
    EntityRule("/v1/uploads/", "upload_id", "upload"),
)

#: Chat routes that name a scope (``scope_kind`` + an entity id) rather than a
#: thread: ``(method, path) -> (where, entity key)``.
CHAT_SCOPE_ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/v1/chat/threads"): ("query", "entity_id"),
    ("POST", "/v1/chat/threads"): ("body", "scope_entity_id"),
    ("PATCH", "/v1/chat/threads/{thread_id}/scope"): ("body", "scope_entity_id"),
}

#: Path prefixes of shared reference data, readable (GET) by any account.
#: Writes under them are BLOCKED: one account must not edit what all see.
SHARED_READ_PREFIXES: tuple[str, ...] = (
    "/v1/library/",
    "/v1/examples",
    "/v1/features",
)


def _body_fields(route: Any) -> set[str]:
    """Top-level JSON field names (and aliases) a route's body accepts."""
    names: set[str] = set()
    for param in getattr(route.dependant, "body_params", []):
        names.add(param.name)
        model = getattr(param.field_info, "annotation", None) or getattr(param, "type_", None)
        fields = getattr(model, "model_fields", None) or {}
        names.update(fields)
        names.update(a for f in fields.values() if (a := getattr(f, "alias", None)))
    return names


def _body_has_project(route: Any) -> bool:
    return bool(_body_fields(route) & set(PROJECT_KEYS))


def _entity_rules(route: Any) -> list[EntityRule]:
    """The entity rules that apply to ``route``, by where its keys appear."""
    names = {p.name for p in route.dependant.path_params}
    names |= {p.name for p in route.dependant.query_params}
    names |= _body_fields(route)
    return [r for r in ENTITY_RULES if route.path.startswith(r.prefix) and r.key in names]


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
    shared = (path + "/").startswith(SHARED_READ_PREFIXES)
    if shared:
        # Reads are shared; writes to what every account sees are not, even
        # when they name a work product (a library job on a shared machine).
        return RouteTenancy.SHARED_READ if method in ("GET", "HEAD") else RouteTenancy.BLOCKED
    if (method, path) in CHAT_SCOPE_ROUTES or _entity_rules(route):
        return RouteTenancy.PROJECT_ENTITY
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


async def _json_body(request: Request) -> dict[str, Any] | None:
    raw = await request.body()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _body_project(data: dict[str, Any] | None) -> str | None:
    if data is None:
        return None
    for key in PROJECT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    # A run request nests it (``POST /v1/runs``); the run store reads it the
    # same way, so the check and the stored run cannot disagree.
    nested = data.get("request")
    if isinstance(nested, dict):
        from api_gateway.runs.schemas import project_of

        return project_of(nested)
    return None


def _entity_ids(
    route: Any,
    path_params: dict[str, Any],
    request: Request,
    body: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    """``(kind, id)`` for every entity the request names."""
    found: list[tuple[str, str]] = []
    for rule in _entity_rules(route):
        value: Any = path_params.get(rule.key)
        if value is None:
            value = request.query_params.get(rule.key)
        if value is None and body is not None:
            value = body.get(rule.key)
        if isinstance(value, str | int) and str(value):
            found.append((rule.kind, str(value)))
    return found


def _not_found(tenancy: RouteTenancy) -> ScopeDecision:
    # 404, not 403: the caller learns nothing about other accounts' ids.
    return ScopeDecision(allowed=False, status=404, tenancy=tenancy, detail="Not found")


async def decide(app: ASGIApp, request: Request, scope: ProjectScope) -> ScopeDecision:
    """Whether ``request`` stays inside ``scope``."""
    from api_gateway.auth import ownership

    if scope.unrestricted:
        return ScopeDecision(allowed=True)
    with tracer.start_as_current_span("auth.project_scope") as span:
        route, path_params = _match(app, request)
        if route is None:
            # No API route matches: let routing answer 404/405 itself.
            return ScopeDecision(allowed=True)
        tenancy = classify_route(request.method, route)
        span.set_attribute("route.tenancy", tenancy.value)

        if tenancy in (
            RouteTenancy.ACCOUNT_LIST,
            RouteTenancy.ACCOUNT_CREATE,
            RouteTenancy.SHARED_READ,
            RouteTenancy.STATELESS,
        ):
            return ScopeDecision(allowed=True, tenancy=tenancy)
        if tenancy is RouteTenancy.TENANT_LIBRARY:
            if scope.tenant:
                return ScopeDecision(allowed=True, tenancy=tenancy)
            return ScopeDecision(
                allowed=False,
                status=403,
                tenancy=tenancy,
                detail="Your account cannot add to the library on this gateway.",
            )
        if tenancy is RouteTenancy.BLOCKED:
            return ScopeDecision(
                allowed=False,
                status=403,
                tenancy=tenancy,
                detail=(
                    f"{request.method} {route.path} is not available on a gateway shared "
                    "by several accounts: it is not yet confined to a project."
                ),
            )

        body = await _json_body(request) if request.method not in ("GET", "HEAD") else None
        pid: str | None = None
        if tenancy is RouteTenancy.PROJECT_PATH:
            pid = next((str(path_params[k]) for k in PROJECT_KEYS if k in path_params), None)
        elif tenancy is RouteTenancy.PROJECT_QUERY:
            pid = _query_project(request)
        elif tenancy is RouteTenancy.PROJECT_BODY:
            pid = _query_project(request) or _body_project(body)

        if tenancy is not RouteTenancy.PROJECT_ENTITY:
            if pid is None:
                return ScopeDecision(
                    allowed=False,
                    status=400,
                    tenancy=tenancy,
                    detail="project_id is required on a gateway shared by several accounts.",
                )
            if not _UUIDISH.match(pid) or not scope.allows(pid):
                return _not_found(tenancy)

        try:
            owners: list[str | None] = []
            chat = CHAT_SCOPE_ROUTES.get((request.method, route.path))
            if chat is not None:
                where, key = chat
                source: Any = request.query_params if where == "query" else (body or {})
                kind, entity = source.get("scope_kind"), source.get(key)
                if not (isinstance(kind, str) and isinstance(entity, str) and entity):
                    return ScopeDecision(
                        allowed=False,
                        status=400,
                        tenancy=tenancy,
                        detail="scope_kind and an entity id are required on a shared gateway.",
                    )
                owners.append(await ownership.chat_scope_project(app, kind, entity))
            entities = _entity_ids(route, path_params, request, body)
            for kind, entity_id in entities:
                if not _UUIDISH.match(entity_id):
                    return _not_found(tenancy)
                owners.append(await ownership.owner_of(app, kind, entity_id))
        except Exception as exc:  # a lookup failing is not "not yours"
            span.record_exception(exc)
            logger.warning("project_scope_lookup_failed", path=route.path, error=str(exc))
            return ScopeDecision(
                allowed=False,
                status=503,
                tenancy=tenancy,
                detail="Could not check which project this belongs to; try again.",
            )

        if tenancy is RouteTenancy.PROJECT_ENTITY and not owners:
            return ScopeDecision(
                allowed=False,
                status=400,
                tenancy=tenancy,
                detail="This route needs an entity id on a gateway shared by several accounts.",
            )
        if any(owner is None or not scope.allows(owner) for owner in owners):
            return _not_found(tenancy)
        if pid is not None:
            span.set_attribute("project.id", pid)
        return ScopeDecision(
            allowed=True, tenancy=tenancy, project_id=pid or (owners[0] if owners else None)
        )
