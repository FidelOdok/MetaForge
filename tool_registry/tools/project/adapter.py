"""MCP adapter exposing project CRUD over the wire (MET-427).

Wraps the project storage layer the gateway already uses
(``api_gateway.projects.backend.ProjectBackend``). To respect the
layer rule (``tool_registry`` may not import from ``api_gateway``)
the adapter defines a structural ``ProjectBackendLike`` protocol; any
gateway backend that satisfies it can be plugged in unchanged.

Five tools today: ``project.create``, ``project.list``,
``project.get``, ``project.update``, ``project.delete``.

Late-binding pattern matches ``KnowledgeServer``: the registry can
register the adapter before the gateway has finished initialising its
backend, and the gateway calls ``set_backend()`` once the runtime
backend is ready.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import structlog

from mcp_core.context import current_context
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.project")

# MET-589: the chat harness silently truncates any tool observation past
# ~8000 chars (orchestrator/harness/native_tools.py::_MAX_OBSERVATION_CHARS),
# dropping trailing list items with no way for the caller to detect it short
# of the length shrinking underneath a `limit` it already asked for. A page
# this small stays comfortably under that cap (with headroom for envelope
# fields and whatever else shares the harness's context budget) regardless
# of how many/how large the requested page's projects turn out to be —
# `has_more`/`next_offset` are trustworthy because the page itself can never
# trigger the harness's shrink.
_MAX_LIST_PAGE_CHARS = 4000


@runtime_checkable
class ProjectLike(Protocol):
    """Structural shape the adapter consumes off a backend's project records.

    ``api_gateway.projects.schemas.ProjectResponse`` satisfies this. Any
    pydantic model with the same attribute surface will, too.
    """

    id: str
    name: str
    description: str
    status: str
    agent_count: int
    created_at: str
    last_updated: str
    work_products: list[Any]


@runtime_checkable
class ProjectBackendLike(Protocol):
    """Subset of ``api_gateway.projects.backend.ProjectBackend`` needed here."""

    async def list_projects(self) -> list[ProjectLike]: ...

    async def get_project(self, project_id: str) -> ProjectLike | None: ...

    async def create_project(
        self,
        *,
        name: str,
        description: str,
        status: str,
    ) -> ProjectLike: ...

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> ProjectLike | None: ...

    async def delete_project(self, project_id: str) -> bool: ...


class ProjectServer(McpToolServer):
    """MCP server adapter around a ``ProjectBackendLike`` instance.

    Constructor takes an optional backend so registry bootstrap can be
    lazy. ``set_backend()`` is the late-binding hook.
    """

    def __init__(self, backend: ProjectBackendLike | None = None) -> None:
        super().__init__(adapter_id="project", version="0.1.0")
        self._backend: ProjectBackendLike | None = backend
        self._register_tools()

    # ------------------------------------------------------------------
    # Late binding
    # ------------------------------------------------------------------

    def set_backend(self, backend: ProjectBackendLike) -> None:
        """Bind a concrete backend after construction."""
        self._backend = backend
        logger.info("project_mcp_backend_bound", backend=type(backend).__name__)

    @property
    def backend(self) -> ProjectBackendLike:
        if self._backend is None:
            raise RuntimeError(
                "ProjectServer.backend was called before set_backend(); "
                "ensure the gateway init wires app.state.project_backend in."
            )
        return self._backend

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="project.create",
                adapter_id="project",
                name="Create Project",
                description=(
                    "Create a new hardware project. Returns the persisted "
                    "project with its generated UUID and timestamps."
                ),
                capability="project_management",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                            "description": "Human-readable project name.",
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 2000,
                            "default": "",
                            "description": "Optional project description.",
                        },
                        "status": {
                            "type": "string",
                            "default": "draft",
                            "description": "Initial project status.",
                        },
                    },
                    "required": ["name"],
                },
                output_schema=_project_output_schema(),
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_create,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="project.list",
                adapter_id="project",
                name="List Projects",
                description=(
                    "Return a page of project summaries (no per-project "
                    "work_products detail — call project.get for that), "
                    "newest first. Paginated via `limit`/`offset`, but the "
                    "actual page may hold FEWER than `limit` even when more "
                    "remain: the response also self-caps to a safe payload "
                    "size. Always check `has_more`/`next_offset` and keep "
                    "re-calling with `next_offset` until `has_more` is "
                    "false to see everything — never assume one call "
                    "returned the full set. Project-level scoping is "
                    "handled by the backend (per-tenant deployments)."
                ),
                capability="project_management",
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 20,
                            "description": "Max projects to return in this page (capped at 100).",
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                            "description": "Number of projects to skip, for paging.",
                        },
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "projects": {
                            "type": "array",
                            "items": _project_summary_schema(),
                        },
                        "total": {
                            "type": "integer",
                            "description": "Total projects visible to the caller, across pages.",
                        },
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "has_more": {
                            "type": "boolean",
                            "description": "True if projects remain beyond this page.",
                        },
                        "next_offset": {
                            "type": ["integer", "null"],
                            "description": (
                                "Pass as `offset` for the next page; null when has_more is false."
                            ),
                        },
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_list,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="project.get",
                adapter_id="project",
                name="Get Project",
                description=(
                    "Fetch a project by UUID, or by exact name when no "
                    "id is supplied. Returns null when not found."
                ),
                capability="project_management",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Project UUID (preferred). Exactly one of `id` or "
                                "`name` must be provided; the handler raises if "
                                "both are absent."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Project name (used when `id` is absent). Exactly "
                                "one of `id` or `name` must be provided."
                            ),
                        },
                    },
                },
                output_schema={
                    "oneOf": [
                        _project_output_schema(),
                        {"type": "null"},
                    ],
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_get,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="project.update",
                adapter_id="project",
                name="Update Project",
                description=(
                    "Rename, redescribe, or change the status of an existing "
                    "project. Only supplied fields change. Raises if the new "
                    "name collides (case-insensitively) with another project."
                ),
                capability="project_management",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Project UUID to update.",
                        },
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                            "description": "New project name.",
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": "New project description.",
                        },
                        "status": {
                            "type": "string",
                            "description": "New project status.",
                        },
                    },
                    "required": ["id"],
                },
                output_schema=_project_output_schema(),
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_update,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="project.delete",
                adapter_id="project",
                name="Delete Project",
                description=(
                    "Permanently delete a project by UUID. Does not delete "
                    "its linked work products from the Digital Twin."
                ),
                capability="project_management",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Project UUID to delete.",
                        },
                    },
                    "required": ["id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "deleted": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_delete,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("project.mcp.create") as span:
            name = arguments.get("name")
            if not name or not isinstance(name, str):
                raise ValueError("project.create: 'name' is required and must be a string")
            description = arguments.get("description", "")
            if description is not None and not isinstance(description, str):
                raise ValueError("project.create: 'description' must be a string when provided")
            status = arguments.get("status", "draft")
            if not isinstance(status, str):
                raise ValueError("project.create: 'status' must be a string")

            ctx = current_context()
            actor_id = ctx.actor_id
            span.set_attribute("project.name", name)
            if actor_id is not None:
                span.set_attribute("mcp.actor_id", str(actor_id))

            project = await self.backend.create_project(
                name=name,
                description=description or "",
                status=status,
            )
            logger.info(
                "project_mcp_create",
                project_id=project.id,
                project_name=name,
                actor_id=str(actor_id) if actor_id is not None else None,
            )
            return _project_to_dict(project)

    async def handle_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("project.mcp.list") as span:
            limit_raw = arguments.get("limit", 20)
            offset_raw = arguments.get("offset", 0)
            if isinstance(limit_raw, bool) or isinstance(offset_raw, bool):
                raise ValueError(
                    "project.list: 'limit' and 'offset' must be integers, not booleans"
                )
            try:
                # Some MCP call paths deliver numeric args as strings (e.g.
                # "20") rather than JSON numbers — coerce like every other
                # numeric-arg adapter in this codebase (memory/knowledge/twin)
                # instead of rejecting valid input on a strict isinstance check.
                limit = int(limit_raw)
                offset = int(offset_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("project.list: 'limit' and 'offset' must be integers") from exc
            if limit < 1:
                raise ValueError("project.list: 'limit' must be a positive integer")
            if offset < 0:
                raise ValueError("project.list: 'offset' must be a non-negative integer")
            limit = min(limit, 100)

            ctx_project_id = current_context().project_id
            projects = await self.backend.list_projects()

            # MET-441: when the call context names a project, scope the
            # list to that project only. Defaults to "no filter" so
            # admin-style callers (no ctx) still see everything.
            if ctx_project_id is not None:
                ctx_id_str = str(ctx_project_id)
                projects = [p for p in projects if p.id == ctx_id_str]
                span.set_attribute("mcp.project_id", ctx_id_str)
                span.set_attribute("project.scoped", True)

            total = len(projects)
            candidates = projects[offset : offset + limit]

            # MET-589: cap the page to a safe serialized size regardless of
            # `limit` — a page that fits `limit` can still be big enough to
            # trip the harness's own truncation, which would silently drop
            # entries with no way for the caller to tell. Always keep at
            # least one project so a single oversized record can't stall
            # pagination entirely.
            page: list[dict[str, Any]] = []
            page_chars = 0
            for p in candidates:
                summary = _project_to_summary_dict(p)
                rendered_len = len(json.dumps(summary))
                if page and page_chars + rendered_len > _MAX_LIST_PAGE_CHARS:
                    break
                page.append(summary)
                page_chars += rendered_len

            returned = len(page)
            has_more = offset + returned < total
            next_offset = offset + returned if has_more else None

            span.set_attribute("project.result_count", returned)
            span.set_attribute("project.total", total)
            logger.info(
                "project_mcp_list",
                result_count=returned,
                total=total,
                offset=offset,
                limit=limit,
                page_chars=page_chars,
                scoped_to=str(ctx_project_id) if ctx_project_id else None,
            )
            return {
                "projects": page,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
                "next_offset": next_offset,
            }

    async def handle_get(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        with tracer.start_as_current_span("project.mcp.get") as span:
            project_id = arguments.get("id")
            project_name = arguments.get("name")

            if project_id is not None:
                if not isinstance(project_id, str):
                    raise ValueError("project.get: 'id' must be a string")
                span.set_attribute("project.lookup_kind", "id")
                project = await self.backend.get_project(project_id)
            elif project_name is not None:
                if not isinstance(project_name, str):
                    raise ValueError("project.get: 'name' must be a string")
                span.set_attribute("project.lookup_kind", "name")
                project = await _find_by_name(self.backend, project_name)
            else:
                raise ValueError("project.get: either 'id' or 'name' must be provided")

            # MET-441: enforce the call context's project boundary. When
            # the looked-up project is not the same as ctx.project_id we
            # treat it as "not found" — leaking the id of an out-of-scope
            # project via a hit/miss difference would be a side-channel.
            if project is not None:
                ctx_project_id = current_context().project_id
                if ctx_project_id is not None and project.id != str(ctx_project_id):
                    span.set_attribute("project.scoped_blocked", True)
                    logger.info(
                        "project_mcp_get_scoped_out",
                        looked_up=project_id or project_name,
                        ctx_project_id=str(ctx_project_id),
                    )
                    return None

            if project is None:
                logger.info("project_mcp_get_not_found", lookup=project_id or project_name)
                return None
            return _project_to_dict(project)

    async def handle_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("project.mcp.update") as span:
            project_id = arguments.get("id")
            if not project_id or not isinstance(project_id, str):
                raise ValueError("project.update: 'id' is required and must be a string")
            name = arguments.get("name")
            description = arguments.get("description")
            status = arguments.get("status")
            if name is None and description is None and status is None:
                raise ValueError(
                    "project.update: at least one of 'name', 'description', "
                    "'status' must be provided"
                )
            if name is not None and not isinstance(name, str):
                raise ValueError("project.update: 'name' must be a string")
            if description is not None and not isinstance(description, str):
                raise ValueError("project.update: 'description' must be a string")
            if status is not None and not isinstance(status, str):
                raise ValueError("project.update: 'status' must be a string")

            span.set_attribute("project.id", project_id)
            project = await self.backend.update_project(
                project_id,
                name=name,
                description=description,
                status=status,
            )
            if project is None:
                raise ValueError(f"project.update: no project with id {project_id!r}")
            logger.info(
                "project_mcp_update",
                project_id=project_id,
                fields=[
                    k
                    for k, v in {"name": name, "description": description, "status": status}.items()
                    if v is not None
                ],
            )
            return _project_to_dict(project)

    async def handle_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("project.mcp.delete") as span:
            project_id = arguments.get("id")
            if not project_id or not isinstance(project_id, str):
                raise ValueError("project.delete: 'id' is required and must be a string")
            span.set_attribute("project.id", project_id)
            deleted = await self.backend.delete_project(project_id)
            if not deleted:
                raise ValueError(f"project.delete: no project with id {project_id!r}")
            logger.info("project_mcp_delete", project_id=project_id)
            return {"id": project_id, "deleted": True}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _find_by_name(backend: ProjectBackendLike, name: str) -> ProjectLike | None:
    """Linear scan over ``list_projects`` for an exact-name match.

    Acceptable for Phase 1 (project counts are O(10s), not O(10ks)).
    When projects grow past that we'll add a name index to the backend.
    """
    projects = await backend.list_projects()
    for project in projects:
        if project.name == name:
            return project
    return None


def _project_to_dict(project: ProjectLike) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "agent_count": project.agent_count,
        "created_at": project.created_at,
        "last_updated": project.last_updated,
        "work_products": [
            {
                "id": getattr(wp, "id", None),
                "name": getattr(wp, "name", None),
                "type": getattr(wp, "type", None),
                "status": getattr(wp, "status", None),
                "updated_at": getattr(wp, "updated_at", None),
            }
            for wp in project.work_products
        ],
    }


def _project_to_summary_dict(project: ProjectLike) -> dict[str, Any]:
    """Lighter per-project projection used by ``project.list`` (MET-589).

    Drops the full ``work_products`` array (unbounded per project — the
    thing that made list pages balloon past the harness's truncation cap)
    in favor of just a count. Callers that need the detail call
    ``project.get``.
    """
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "agent_count": project.agent_count,
        "created_at": project.created_at,
        "last_updated": project.last_updated,
        "work_product_count": len(project.work_products),
    }


def _project_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string"},
            "agent_count": {"type": "integer"},
            "created_at": {"type": "string"},
            "last_updated": {"type": "string"},
            "work_products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "status": {"type": "string"},
                        "updated_at": {"type": "string"},
                    },
                },
            },
        },
    }


def _project_summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string"},
            "agent_count": {"type": "integer"},
            "created_at": {"type": "string"},
            "last_updated": {"type": "string"},
            "work_product_count": {"type": "integer"},
        },
    }
