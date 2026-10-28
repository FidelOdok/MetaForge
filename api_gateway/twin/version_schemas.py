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


class GeometryDiffResponse(BaseModel):
    """Real volume/area/bounding-box delta vs. a SUPERSEDES predecessor
    (FORGE-301) -- distinct from ``RevisionDiff`` above, which diffs one
    node's own metadata revisions and never sees geometry changes.

    ``has_previous_version`` is false for a work product that has never
    been superseded, which is almost all of them: that is an ordinary
    answer, not an error, so the route returns it as a 200 with the
    measured fields left unset (FORGE-709), the same way
    ``/nodes/{id}/versions`` answers an empty history with ``200`` and an
    empty list rather than a 404.
    """

    current_work_product_id: str
    has_previous_version: bool = True
    previous_work_product_id: str | None = None
    current_volume_mm3: float | None = None
    previous_volume_mm3: float | None = None
    volume_delta_mm3: float | None = None
    current_area_mm2: float | None = None
    previous_area_mm2: float | None = None
    area_delta_mm2: float | None = None
    current_bounding_box: dict[str, Any] | None = None
    previous_bounding_box: dict[str, Any] | None = None


class InterferenceCheckResponse(BaseModel):
    """Real boolean-intersection result between two named parts (FORGE-272)
    -- a pairwise clearance/interference check, not an ISO 286 fit
    classification (which this capability deliberately does not attempt;
    see ``api_gateway.twin.interference_check``'s module docstring)."""

    work_product_id_a: str
    work_product_id_b: str
    interferes: bool
    interference_volume_mm3: float
    interference_area_mm2: float
