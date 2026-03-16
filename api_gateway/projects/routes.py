"""Projects REST endpoints for the MetaForge Gateway.

Provides CRUD operations on hardware projects.  Storage is delegated
to a ``ProjectBackend`` — either PostgreSQL (when ``DATABASE_URL`` is
set) or an in-memory fallback.

Endpoints live under ``/v1/projects``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.projects.backend import InMemoryProjectBackend, ProjectBackend
from api_gateway.projects.schemas import (
    CreateProjectRequest,
    ProjectListResponse,
    ProjectResponse,
)
from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.projects")

# ---------------------------------------------------------------------------
# Twin integration (initialised by server lifespan)
# ---------------------------------------------------------------------------

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default InMemoryTwinAPI with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("projects_twin_initialized", twin_type=type(twin).__name__)


# ---------------------------------------------------------------------------
# Backend (initialised by server lifespan)
# ---------------------------------------------------------------------------

_backend: ProjectBackend = InMemoryProjectBackend.create()

# Legacy alias — kept for backward compat with tests and chat routes
# that import `store` for project_store.projects access
store = _backend


def init_project_backend(backend: ProjectBackend) -> None:
    """Replace the default in-memory backend with a production backend."""
    global _backend, store  # noqa: PLW0603
    _backend = backend
    store = backend
    logger.info("project_backend_initialized", backend_type=type(backend).__name__)


async def link_work_product_to_project(
    project_id: str,
    wp_id: str,
    wp_name: str,
    wp_type: str,
) -> None:
    """Add a WorkProduct reference to an existing project."""
    await _backend.link_work_product(project_id, wp_id, wp_name, wp_type)


router = APIRouter(prefix="/v1/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ProjectListResponse)
async def list_projects() -> ProjectListResponse:
    """List all hardware projects."""
    with tracer.start_as_current_span("projects.list"):
        projects = await _backend.list_projects()
        logger.info("projects_listed", count=len(projects))
        return ProjectListResponse(projects=projects, total=len(projects))


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str) -> ProjectResponse:
    """Get a single project by ID."""
    with tracer.start_as_current_span("projects.get") as span:
        span.set_attribute("project.id", project_id)
        project = await _backend.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest) -> ProjectResponse:
    """Create a new hardware project and seed an initial WorkProduct."""
    with tracer.start_as_current_span("projects.create") as span:
        project = await _backend.create_project(
            name=body.name,
            description=body.description,
            status=body.status,
        )
        span.set_attribute("project.id", project.id)

        # Seed an initial CAD_MODEL WorkProduct in the Twin
        seed_wp = WorkProduct(
            name=f"{body.name} - CAD Model",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="",
            format="step",
            created_by="project-setup",
            metadata={"project_id": project.id},
        )
        created_wp = await _twin.create_work_product(seed_wp)

        await _backend.link_work_product(
            project.id,
            str(created_wp.id),
            created_wp.name,
            created_wp.type.value if hasattr(created_wp.type, "value") else str(created_wp.type),
        )

        # Re-fetch to include the work product
        project = await _backend.get_project(project.id)
        if project is None:
            raise HTTPException(status_code=500, detail="Project creation failed")

        logger.info(
            "project_created",
            project_id=project.id,
            name=body.name,
            seed_wp_id=str(created_wp.id),
        )
        return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str) -> None:
    """Delete a project by ID."""
    with tracer.start_as_current_span("projects.delete") as span:
        span.set_attribute("project.id", project_id)
        deleted = await _backend.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Project not found")
        logger.info("project_deleted", project_id=project_id)
