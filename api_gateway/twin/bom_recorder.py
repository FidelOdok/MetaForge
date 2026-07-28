"""BOM work-product recorder for the electronics phase (MET-10).

The electronics correctness rubric (evals/electronics_rubric.py) requires a real
BOM *artifact*, not a prose "BOM plan" — the electronics analog of "real FEA, not
a hand-calc". A brand-new board has no KiCad schematic to export a BOM from
(Phase-1 KiCad is read-only), but a bill of materials is just a structured list
of the selected components, which the electronics design already determines. So
this recorder persists that component list as a loadable ``bom`` work product,
mirroring :func:`make_geometry_recorder`:

1. store the CSV blob in MinIO (graceful — node still created on failure),
2. create a validated ``BOM`` WorkProduct with ``content_hash`` +
   ``metadata["minio_object_key"]`` so it is loadable/viewable like any artifact,
3. link it to its project so it shows on the Projects page.
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
tracer = get_tracer("api_gateway.twin.bom_recorder")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "bom")[:60]


def bom_csv(rows: list[dict[str, Any]]) -> str:
    """Render component rows as a stable CSV (ref,part,value,qty,function)."""
    cols = ("ref", "part", "value", "qty", "function")
    out = [",".join(cols)]
    for r in rows:
        cells = []
        for c in cols:
            v = str(r.get(c, "")).replace('"', "'")
            cells.append(f'"{v}"' if ("," in v or " " in v) else v)
        out.append(",".join(cells))
    return "\n".join(out) + "\n"


def make_bom_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` that persists a loadable BOM work product."""

    async def record(
        *,
        rows: list[dict[str, Any]],
        name: str,
        project_id: str | None = None,
        session_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("bom recorder: 'name' is required (non-empty string)")
        if not rows:
            raise ValueError("bom recorder: at least one component row is required")

        content = bom_csv(rows).encode("utf-8")
        with tracer.start_as_current_span("electronics.record_bom") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(content).hexdigest()
            filename = f"{_slug(name)}.csv"
            span.set_attribute("bom.name", name)
            span.set_attribute("bom.rows", len(rows))

            # 1. blob → MinIO (graceful: keep the node even if storage is down).
            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id), filename, content, content_type="text/csv"
                )
            except Exception as exc:  # noqa: BLE001 — degrade like the geometry recorder
                logger.warning("bom_blob_store_skipped", name=name, error=str(exc))

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "content_sha256": content_hash,
                "authored_by": "electronics.design",
                "line_items": len(rows),
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key
            if session_id:
                metadata["session_id"] = session_id
            if extra_metadata:
                metadata.update(extra_metadata)

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.BOM,
                domain="electronics",
                file_path="",
                content_hash=content_hash,
                format="csv",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by="electronics.design",
                project_id=project_id,
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            # 2. project junction link so it shows on the Projects page.
            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, name, "bom")
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("bom_project_link_failed", error=str(exc))

            logger.info(
                "bom_recorded",
                node_id=node_id,
                project_id=project_id,
                linked=linked,
                line_items=len(rows),
            )
            return {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "line_items": len(rows),
                "project_linked": linked,
            }

    return record
