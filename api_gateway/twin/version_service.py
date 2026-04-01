"""Version service — records and retrieves per-work-product revision history.

Revisions are stored as a list under ``metadata["_revisions"]``.  Each entry
is a lightweight snapshot: revision number, timestamp, content hash, change
description, and the non-internal metadata fields at that point in time.

This is deliberately simple — no separate node type, no extra graph edges for
the in-memory backend.  The ``SUPERSEDES`` edge type is reserved for the
Neo4j backend and for cross-work-product lineage (e.g. a new STEP file
supersedes a previous BOM).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from api_gateway.twin.version_schemas import (
    FieldDelta,
    RevisionDiff,
    WorkProductRevision,
    WorkProductVersionHistory,
)
from observability.tracing import get_tracer
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.version_service")

# Metadata keys that are internal bookkeeping — excluded from snapshots
_INTERNAL_KEYS = frozenset({"_revisions"})


class VersionService:
    """Static helpers for reading and writing revision history."""

    @staticmethod
    def build_revision(
        wp: WorkProduct,
        change_description: str,
        snapshot_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a new revision dict from the current work product state.

        If ``snapshot_override`` is provided it is used as the metadata
        snapshot (internal keys stripped).  This lets the iterate endpoint
        record the post-update state rather than the pre-update state.

        Does NOT mutate the work product — call ``append_to_metadata`` to
        actually persist it.
        """
        existing: list[dict[str, Any]] = wp.metadata.get("_revisions", [])
        revision_number = len(existing) + 1
        raw_snapshot = snapshot_override if snapshot_override is not None else wp.metadata
        snapshot = {k: v for k, v in raw_snapshot.items() if k not in _INTERNAL_KEYS}
        return {
            "revision": revision_number,
            "created_at": datetime.now(UTC).isoformat(),
            "content_hash": wp.content_hash,
            "change_description": change_description,
            "metadata_snapshot": snapshot,
        }

    @staticmethod
    def append_to_metadata(
        existing_metadata: dict[str, Any],
        revision: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a new metadata dict with the revision appended."""
        revisions: list[dict[str, Any]] = list(existing_metadata.get("_revisions", []))
        revisions.append(revision)
        return {**existing_metadata, "_revisions": revisions}

    @staticmethod
    def get_history(wp: WorkProduct) -> WorkProductVersionHistory:
        """Extract revision history from a work product's metadata."""
        raw: list[dict[str, Any]] = wp.metadata.get("_revisions", [])
        revisions = [WorkProductRevision(**r) for r in raw]
        return WorkProductVersionHistory(
            work_product_id=str(wp.id),
            revisions=revisions,
            total=len(revisions),
        )

    @staticmethod
    def diff(
        history: WorkProductVersionHistory,
        revision_a: int,
        revision_b: int,
    ) -> RevisionDiff:
        """Compute a metadata diff between two revisions (1-indexed).

        ``revision_a`` is treated as "before" and ``revision_b`` as "after".
        Raises ``ValueError`` for out-of-range revision numbers.
        """
        if revision_a < 1 or revision_a > history.total:
            raise ValueError(
                f"revision_a={revision_a} out of range [1, {history.total}]"
            )
        if revision_b < 1 or revision_b > history.total:
            raise ValueError(
                f"revision_b={revision_b} out of range [1, {history.total}]"
            )

        meta_a = history.revisions[revision_a - 1].metadata_snapshot
        meta_b = history.revisions[revision_b - 1].metadata_snapshot

        keys_a = set(meta_a)
        keys_b = set(meta_b)

        changed: dict[str, FieldDelta] = {}
        for key in keys_a & keys_b:
            if meta_a[key] != meta_b[key]:
                changed[key] = FieldDelta(from_value=meta_a[key], to_value=meta_b[key])

        added = {k: meta_b[k] for k in keys_b - keys_a}
        removed = {k: meta_a[k] for k in keys_a - keys_b}

        return RevisionDiff(
            work_product_id=history.work_product_id,
            revision_a=revision_a,
            revision_b=revision_b,
            changed=changed,
            added=added,
            removed=removed,
        )
