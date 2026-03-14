"""Request/response schemas for projects REST endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectWorkProductResponse(BaseModel):
    """A single work product within a project."""

    id: str
    name: str
    type: str
    status: str
    updated_at: str


class ProjectResponse(BaseModel):
    """Dashboard-friendly representation of a hardware project."""

    id: str
    name: str
    description: str
    status: str
    work_products: list[ProjectWorkProductResponse] = Field(default_factory=list)
    agent_count: int = 0
    last_updated: str
    created_at: str


class ProjectListResponse(BaseModel):
    """List of projects."""

    projects: list[ProjectResponse]
    total: int
