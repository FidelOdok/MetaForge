"""Design-sketch persistence: a real, versioned Twin approval gate before
committing to CAD/build work (follow-up to MET-740/747).

Prime Rule ("if it can't be versioned, reviewed, and built, MetaForge doesn't
output it") applies to the *gate itself*, not just the thing it gates: a
proportions/topology reference sketch that's meant to actually block a build
decision belongs in the Digital Twin as a real work product, not a throwaway
local file — otherwise the approval it represents isn't reviewable or
auditable after the fact.

Mirrors ``robot_description_recorder.py``'s pattern (MinIO blob + structured
metadata + project link) with two differences:

1. **Single blob, no mesh files**: a design sketch is one self-contained
   ``.html`` document (inline CSS/JS, no external assets per the artifact
   authoring convention) — nothing analogous to per-link mesh files.
2. **Has an approval gate, not just revisions**: ``metadata.approved``/
   ``metadata.approved_at`` track whether a human has signed off on the
   sketch. ``approve()`` is a dedicated state transition (not a content
   edit), versioned the same way ``robot_description_recorder.update()``
   versions a re-export.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.design_sketch_recorder")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "sketch")[:60]


def make_design_sketch_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``commit(...)`` bound to a twin + project backend."""

    async def commit(
        *,
        name: str,
        html_content: str,
        description_text: str = "",
        source_node_ids: list[str] | None = None,
        project_id: str | None = None,
        domain: str = "mechanical",
        source_tool: str = "decide_sketch_needed",
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("design_sketch commit: 'name' is required (non-empty string)")
        content = html_content.encode("utf-8")
        if not content:
            raise ValueError("design_sketch commit: 'html_content' is empty")

        with tracer.start_as_current_span("twin.commit_design_sketch") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(content).hexdigest()
            filename = f"{_slug(name)}.html"
            span.set_attribute("design_sketch.name", name)
            span.set_attribute("design_sketch.source_count", len(source_node_ids or []))

            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id), filename, content, content_type="text/html; charset=utf-8"
                )
            except Exception as exc:  # noqa: BLE001 — degrade like robot_description_recorder
                logger.warning("design_sketch_blob_store_skipped", wp_id=str(wp_id), error=str(exc))

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "content_sha256": content_hash,
                "authored_by": source_tool,
                "description_text": description_text,
                "approved": False,
                "approved_at": None,
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.DESIGN_SKETCH,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format="html",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=source_tool,
                project_id=project_id,  # pydantic coerces str -> UUID
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            # PARENT_OF edges to whatever existing work product(s) this
            # sketch reviews (the revision case — nothing to link for a
            # brand-new design, since nothing's built yet). Best-effort per
            # edge, mirrors robot_description_recorder's source-part edges.
            edge_failures = 0
            for source_id in source_node_ids or []:
                try:
                    await twin.add_edge(created.id, source_id, EdgeType.PARENT_OF)
                except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                    edge_failures += 1
                    logger.warning(
                        "design_sketch_source_edge_failed",
                        node_id=node_id,
                        source_id=source_id,
                        error=str(exc),
                    )

            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, node_id, name, "design_sketch"
                    )
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("design_sketch_project_link_failed", error=str(exc))

            logger.info(
                "design_sketch_committed",
                node_id=node_id,
                project_id=project_id,
                linked=linked,
                minio_object_key=minio_object_key,
                source_count=len(source_node_ids or []),
                edge_failures=edge_failures,
            )
            return {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "project_linked": linked,
            }

    return commit


def make_design_sketch_approver(twin: Any) -> Any:
    """Return an async ``approve(node_id, ...)`` bound to a twin.

    A dedicated state transition (approved: false -> true), not a content
    edit — versioned the same way ``robot_description_recorder.update()``
    versions a re-export, so "who approved this and when" stays in the
    node's real revision history rather than being silently overwritten.
    """

    async def approve(
        node_id: str,
        *,
        approved_by: str | None = None,
        change_description: str = "sketch approved for build",
    ) -> dict[str, Any]:
        from uuid import UUID as _UUID

        from api_gateway.twin.version_service import VersionService

        with tracer.start_as_current_span("twin.approve_design_sketch") as span:
            span.set_attribute("design_sketch.node_id", node_id)
            uid = _UUID(node_id)
            wp = await twin.get_work_product(uid)
            if wp is None:
                raise ValueError(f"design_sketch approve: node {node_id} not found")
            if wp.metadata.get("approved"):
                raise ValueError(f"design_sketch approve: node {node_id} is already approved")

            updated_meta = dict(wp.metadata)
            updated_meta["approved"] = True
            updated_meta["approved_at"] = datetime.now(UTC).isoformat()
            if approved_by:
                updated_meta["approved_by"] = approved_by

            revision = VersionService.build_revision(
                wp, change_description, snapshot_override=updated_meta
            )
            final_meta = VersionService.append_to_metadata(updated_meta, revision)
            await twin.update_work_product(
                uid, {"metadata": final_meta, "updated_at": datetime.now(UTC)}
            )

            logger.info(
                "design_sketch_approved",
                node_id=node_id,
                approved_by=approved_by,
                revision_number=revision.get("revision"),
            )
            return {
                "node_id": node_id,
                "approved": True,
                "approved_at": updated_meta["approved_at"],
                "revision": revision,
            }

    return approve
