"""MCP adapter exposing project CRUD over the wire (MET-427).

Wraps the project storage layer the gateway already uses
(``api_gateway.projects.backend.ProjectBackend``). To respect the
layer rule (``tool_registry`` may not import from ``api_gateway``)
the adapter defines a structural ``ProjectBackendLike`` protocol; any
gateway backend that satisfies it can be plugged in unchanged.

Three tools today: ``project.create``, ``project.list``,
``project.get``.

Late-binding pattern matches ``KnowledgeServer``: the registry can
register the adapter before the gateway has finished initialising its
backend, and the gateway calls ``set_backend()`` once the runtime
backend is ready.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import structlog

from mcp_core.context import current_context
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.project")


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
                    "Return every project the caller can see. No filter "
                    "args today; project-level scoping is handled by the "
                    "backend (per-tenant deployments)."
                ),
                capability="project_management",
                input_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "properties": {
                        "projects": {
                            "type": "array",
                            "items": _project_output_schema(),
                        },
                        "total": {"type": "integer"},
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
                            "description": "Project UUID (preferred).",
                        },
                        "name": {
                            "type": "string",
                            "description": "Project name (used when id is absent).",
                        },
                    },
                    "anyOf": [
                        {"required": ["id"]},
                        {"required": ["name"]},
                    ],
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
            projects = await self.backend.list_projects()
            span.set_attribute("project.result_count", len(projects))
            logger.info("project_mcp_list", result_count=len(projects))
            return {
                "projects": [_project_to_dict(p) for p in projects],
                "total": len(projects),
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

            if project is None:
                logger.info("project_mcp_get_not_found", lookup=project_id or project_name)
                return None
            return _project_to_dict(project)


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
