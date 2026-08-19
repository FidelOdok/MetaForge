"""Materialize a committed work product's blob onto the shared adapter workspace (MET-618).

Every CAD/FEA/PCB adapter tool (``freecad.get_properties``, ``freecad.open_session``
+ an import step, ``cadquery.*``, ``calculix.*``) takes a filesystem ``input_file``
path, not a Twin node id or MinIO key. Once a work product's *authoring* session
(``freecad.open_session`` etc.) is closed or evicted after its idle TTL, an agent
had no way back to the actual geometry — ``freecad.describe_session`` fails, the
node's ``file_path`` is empty (MinIO is the source of truth, see
``blob_store.resolve_work_product_blob``), and every inspection tool needs a local
path it doesn't have. This mirrors ``api_gateway.twin.boolean_ops``' scratch-file
pattern (resolve blob -> write into ``ADAPTER_WORKSPACE_DIR``) but, unlike that
one-shot flow, does not clean up afterward: the staged path is meant to be reused
across several follow-up tool calls in the same inspection.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.blob_stager")

_STAGE_SUBDIR = "_staged_work_products"


def make_blob_stager(twin: Any, *, workspace_dir: Path | None = None) -> Any:
    """Return an async ``stage(node_id) -> dict`` bound to ``twin``.

    ``workspace_dir`` overrides ``ADAPTER_WORKSPACE_DIR`` (default
    ``/workspace``) — the directory every adapter container mounts the same
    volume at, so a path written here is immediately loadable by any of them.
    """

    root = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))

    async def stage(node_id: str) -> dict[str, Any]:
        from api_gateway.twin.blob_store import resolve_work_product_blob

        with tracer.start_as_current_span("twin.stage_work_product_file") as span:
            span.set_attribute("twin.node_id", node_id)
            try:
                uid = UUID(node_id)
            except ValueError as exc:
                raise ValueError(f"Invalid node id: {node_id!r}") from exc

            wp = await twin.get_work_product(uid)
            if wp is None:
                raise ValueError(f"Work product not found: {node_id}")

            content, filename = resolve_work_product_blob(wp)

            stage_dir = root / _STAGE_SUBDIR / str(uid)
            stage_dir.mkdir(parents=True, exist_ok=True)
            file_path = stage_dir / filename
            # Work products are immutable once committed, so an existing file
            # of the right size is already correct — skip the rewrite on the
            # common case where the same node is staged again this session.
            if not file_path.exists() or file_path.stat().st_size != len(content):
                file_path.write_bytes(content)

            span.set_attribute("file.size", len(content))
            logger.info(
                "work_product_staged",
                node_id=node_id,
                filename=filename,
                file_path=str(file_path),
                size_bytes=len(content),
            )
            return {
                "node_id": node_id,
                "file_path": str(file_path),
                "filename": filename,
                "size_bytes": len(content),
                "content_hash": wp.content_hash,
                "format": wp.format,
            }

    return stage
