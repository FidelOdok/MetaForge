"""Projects REST endpoints for the MetaForge Gateway.

Provides CRUD operations on hardware projects.  Uses an in-memory
store seeded with demo data that matches the dashboard mock data.

Endpoints live under ``/api/v1/projects``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.projects.schemas import (
    ProjectArtifactResponse,
    ProjectListResponse,
    ProjectResponse,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.projects")

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# In-memory store (same pattern as ChatStore)
# ---------------------------------------------------------------------------

_now = datetime.now(UTC)


class InMemoryProjectStore:
    """Dict-backed project storage seeded with demo projects."""

    def __init__(self) -> None:
        self.projects: dict[str, ProjectResponse] = {}

    @classmethod
    def create(cls) -> InMemoryProjectStore:
        store = cls()
        for proj in _SEED_PROJECTS:
            store.projects[proj.id] = proj
        return store


_SEED_PROJECTS: list[ProjectResponse] = [
    ProjectResponse(
        id="proj-001",
        name="Drone Flight Controller",
        description="STM32-based flight controller with IMU, barometer, and GPS integration.",
        status="active",
        agent_count=3,
        last_updated=(_now - timedelta(hours=2)).isoformat(),
        created_at=(_now - timedelta(days=7)).isoformat(),
        artifacts=[
            ProjectArtifactResponse(
                id="art-001",
                name="Main Schematic",
                type="schematic",
                status="valid",
                updated_at=_now.isoformat(),
            ),
            ProjectArtifactResponse(
                id="art-002",
                name="PCB Layout",
                type="pcb",
                status="warning",
                updated_at=_now.isoformat(),
            ),
            ProjectArtifactResponse(
                id="art-003",
                name="Enclosure CAD",
                type="cad_model",
                status="valid",
                updated_at=_now.isoformat(),
            ),
        ],
    ),
    ProjectResponse(
        id="proj-002",
        name="IoT Sensor Hub",
        description="ESP32-based sensor aggregation board with LoRa and WiFi connectivity.",
        status="active",
        agent_count=2,
        last_updated=(_now - timedelta(days=1)).isoformat(),
        created_at=(_now - timedelta(days=14)).isoformat(),
        artifacts=[
            ProjectArtifactResponse(
                id="art-004",
                name="Sensor Board Schematic",
                type="schematic",
                status="valid",
                updated_at=_now.isoformat(),
            ),
            ProjectArtifactResponse(
                id="art-005",
                name="Firmware",
                type="firmware",
                status="unknown",
                updated_at=_now.isoformat(),
            ),
        ],
    ),
    ProjectResponse(
        id="proj-003",
        name="Power Supply Module",
        description="High-efficiency buck converter module for 5V/3.3V output.",
        status="draft",
        agent_count=0,
        last_updated=(_now - timedelta(days=3)).isoformat(),
        created_at=(_now - timedelta(days=3)).isoformat(),
        artifacts=[],
    ),
]

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
