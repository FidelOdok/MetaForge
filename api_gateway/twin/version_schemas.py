"""Response schemas for work product version history (MET-251)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WorkProductRevision(BaseModel):
    """A single snapshot in a work product's revision history."""

    revision: int
    created_at: str
    content_hash: str
    change_description: str
    metadata_snapshot: dict[str, Any]


class WorkProductVersionHistory(BaseModel):
    """Full version history for a work product."""

    work_product_id: str
    revisions: list[WorkProductRevision]
    total: int


class FieldDelta(BaseModel):
    """Change for a single metadata field."""

    from_value: Any
    to_value: Any


class RevisionDiff(BaseModel):
    """Metadata diff between two revisions."""

    work_product_id: str
    revision_a: int
    revision_b: int
    changed: dict[str, FieldDelta]
    added: dict[str, Any]
    removed: dict[str, Any]


class IterateRequest(BaseModel):
    """Request body for POST /v1/twin/nodes/{id}/iterate."""

    change_description: str
    metadata_updates: dict[str, Any] = {}
