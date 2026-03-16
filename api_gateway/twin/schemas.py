"""Pydantic response schemas for the Digital Twin viewer endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class TwinNodeResponse(BaseModel):
    """Single node in the Digital Twin graph, shaped for the dashboard."""

    id: str
    name: str
    type: str
    domain: str
    status: str
    properties: dict[str, str | int | float | bool]
    updatedAt: str  # noqa: N815


class TwinNodeListResponse(BaseModel):
    """Paginated list of twin nodes."""

    nodes: list[TwinNodeResponse]
    total: int
