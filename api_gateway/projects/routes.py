"""Projects REST endpoints for the MetaForge Gateway.

Provides CRUD operations on hardware projects.  Uses an in-memory
store that starts empty — projects are created via the API.

Endpoints live under ``/v1/projects``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.projects.schemas import (
    CreateProjectRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectWorkProductResponse,
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


def link_work_product_to_project(
    project_id: str,
    wp_id: str,
    wp_name: str,
    wp_type: str,
) -> None:
    """Add a WorkProduct reference to an existing project's work_products list."""
    project = store.projects.get(project_id)
    if project is None:
        return
    now = datetime.now(UTC).isoformat()
    project.work_products.append(
        ProjectWorkProductResponse(
            id=wp_id,
            name=wp_name,
            type=wp_type,
            status="created",
            updated_at=now,
        )
    )
    project.last_updated = now
    logger.info(
        "work_product_linked_to_project",
        project_id=project_id,
        work_product_id=wp_id,
    )


router = APIRouter(prefix="/v1/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# In-memory store (starts empty — no seed data)
# ---------------------------------------------------------------------------


class InMemoryProjectStore:
    """Dict-backed project storage."""

    def __init__(self) -> None:
        self.projects: dict[str, ProjectResponse] = {}

    @classmethod
    def create(cls) -> InMemoryProjectStore:
        return cls()


store = InMemoryProjectStore.create()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ProjectListResponse)
def list_projects() -> ProjectListResponse:
    """List all hardware projects."""
    with tracer.start_as_current_span("projects.list"):
        projects = list(store.projects.values())
        logger.info("projects_listed", count=len(projects))
        return ProjectListResponse(projects=projects, total=len(projects))


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str) -> ProjectResponse:
    """Get a single project by ID."""
    with tracer.start_as_current_span("projects.get") as span:
        span.set_attribute("project.id", project_id)
        project = store.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest) -> ProjectResponse:
    """Create a new hardware project and seed an initial WorkProduct."""
    with tracer.start_as_current_span("projects.create") as span:
        now = datetime.now(UTC).isoformat()
        project_id = str(uuid4())
        span.set_attribute("project.id", project_id)

        project = ProjectResponse(
            id=project_id,
            name=body.name,
            description=body.description,
            status=body.status,
            work_products=[],
            agent_count=0,
            last_updated=now,
            created_at=now,
        )

        # Seed an initial CAD_MODEL WorkProduct in the Twin
        seed_wp = WorkProduct(
            name=f"{body.name} - CAD Model",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="",
            format="step",
            created_by="project-setup",
            metadata={"project_id": project_id},
        )
        created_wp = await _twin.create_work_product(seed_wp)
        project.work_products.append(
            ProjectWorkProductResponse(
                id=str(created_wp.id),
                name=created_wp.name,
                type=(
                    created_wp.type.value
                    if hasattr(created_wp.type, "value")
                    else str(created_wp.type)
                ),
                status="created",
                updated_at=now,
            )
        )

        store.projects[project_id] = project
        logger.info(
            "project_created",
            project_id=project_id,
            name=body.name,
            seed_wp_id=str(created_wp.id),
        )
        return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    """Delete a project by ID."""
    with tracer.start_as_current_span("projects.delete") as span:
        span.set_attribute("project.id", project_id)
        if project_id not in store.projects:
            raise HTTPException(status_code=404, detail="Project not found")
        del store.projects[project_id]
        logger.info("project_deleted", project_id=project_id)
