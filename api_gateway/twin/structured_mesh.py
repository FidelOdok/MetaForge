"""The structured 3D mesh (L1) of a concept, stored in the twin (C3D slice 2).

``tool_registry/tools/twin/layout_mesh.py`` turns a concept block layout into
a GLB with one node per component. This records it: a ``render`` work product
in GLB with ``authority: structured`` and a representation descriptor
(``level: L1``, dimensions estimated, the source layout revision and the
component ids it shows), linked to the layout sketch it was drawn from.

It is fast and free (no provider), so it runs inline, not as a job. It is
made once per layout and assembly: when the same GLB already exists in the
project (same content hash, so the same layout and the same component ids),
that one is returned and nothing is written.

Which layout: the one named (``layout_node_id``), an inline one, or else the
project's newest sketch drawn by ``twin.render_concept_sketch``.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.component_identity import issue_ids, project_identity
from twin_core.representation import (
    PART_COMPONENT_IDS_KEY,
    REPRESENTATION_KEY,
    STRUCTURED_MESH_TOOL,
    RepresentationAuthority,
    RepresentationLevel,
    descriptor,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.structured_mesh")

TOOL = STRUCTURED_MESH_TOOL
GLB_MIME = "model/gltf-binary"
LABEL = "Simple 3D parts, sizes estimated"

_metrics: Any = None
#: The blob writer used when a recorder is given none (tests install one).
_blob_writer: Callable[..., str] | None = None


def set_blob_writer(writer: Callable[..., str] | None) -> None:
    """Install the default blob writer (tests); ``None`` restores the twin's store."""
    global _blob_writer  # noqa: PLW0603
    _blob_writer = writer


class StructuredMeshError(ValueError):
    """The mesh could not be made or stored. The message is caller-facing."""


class NoLayoutError(StructuredMeshError):
    """The project has no concept block layout to build from."""


def _record(outcome: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_structured_mesh(outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a mesh
        pass


def _wp_type(wp: Any) -> str:
    t = getattr(wp, "type", None)
    return str(getattr(t, "value", t) or "")


def _item_ref(meta: dict[str, Any]) -> str | None:
    if meta.get("item_key") and isinstance(meta.get("item_revision"), int):
        return f"{meta['item_key']}@{meta['item_revision']}"
    return None


def make_structured_mesh_recorder(
    twin: Any,
    project_backend: Any = None,
    *,
    blob_writer: Callable[..., str] | None = None,
) -> Any:
    """Return an async ``render(...)`` that builds, stores and links one L1 mesh.

    ``blob_writer(node_id, filename, content, content_type=...) -> key`` is
    injectable for tests and defaults to the twin's blob store.
    """

    def _default_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key

    def write_blob(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        writer = blob_writer or _blob_writer or _default_writer
        return writer(node_id, filename, content, content_type=content_type)

    async def _layout(
        project_id: UUID | None, layout_node_id: str | None, inline: dict[str, Any] | None
    ) -> tuple[Any, Any | None]:
        from tool_registry.tools.twin.concept_sketch import parse_layout
        from twin_core.models.enums import WorkProductType

        if inline is not None:
            try:
                return parse_layout(inline), None
            except ValueError as exc:
                raise StructuredMeshError(f"bad layout: {exc}") from exc
        if layout_node_id:
            try:
                node_id = UUID(str(layout_node_id))
            except ValueError as exc:
                raise StructuredMeshError(f"not a node id: {layout_node_id!r}") from exc
            wp = await twin.get_work_product(node_id)
            stored = (getattr(wp, "metadata", None) or {}).get("concept_layout") if wp else None
            if wp is None or not isinstance(stored, dict):
                raise StructuredMeshError(
                    f"{layout_node_id} is not a concept sketch with a block layout"
                )
            candidates = [wp]
        else:
            if project_id is None:
                raise NoLayoutError("a project or a layout is needed")
            sketches = await twin.list_work_products(
                work_product_type=WorkProductType.DESIGN_SKETCH, project_id=project_id
            )
            candidates = sorted(
                (s for s in sketches if isinstance((s.metadata or {}).get("concept_layout"), dict)),
                key=lambda s: s.created_at.isoformat() if s.created_at else "",
                reverse=True,
            )
        for wp in candidates:
            try:
                return parse_layout(wp.metadata["concept_layout"]), wp
            except ValueError as exc:
                logger.warning("structured_mesh_layout_invalid", node_id=str(wp.id), error=str(exc))
        raise NoLayoutError(
            "there is no concept block layout yet; draw a concept sketch "
            "(twin.render_concept_sketch) first"
        )

    async def _existing(project_id: UUID | None, content_hash: str) -> Any | None:
        from twin_core.models.enums import WorkProductType

        if project_id is None:
            return None
        try:
            renders = await twin.list_work_products(
                work_product_type=WorkProductType.RENDER, project_id=project_id
            )
        except Exception:  # noqa: BLE001
            return None
        for wp in renders:
            meta = getattr(wp, "metadata", None) or {}
            if meta.get("generated_by") == TOOL and wp.content_hash == content_hash:
                return wp
        return None

    async def render(
        *,
        project_id: str | None = None,
        layout_node_id: str | None = None,
        layout: dict[str, Any] | None = None,
        name: str | None = None,
        domain: str = "mechanical",
        trigger: str = "tool",
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        from tool_registry.tools.twin.layout_mesh import build_structured_mesh
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        pid = UUID(str(project_id)) if project_id else None
        with tracer.start_as_current_span("twin.render_structured_mesh") as span:
            span.set_attribute("structured_mesh.trigger", trigger)
            try:
                parsed, source = await _layout(pid, layout_node_id, layout)
            except NoLayoutError:
                _record("no_layout")
                raise
            except StructuredMeshError:
                _record("failed")
                raise
            ident = await project_identity(twin, pid) if pid else {"name_to_id": {}}
            names = [p.name for p in parsed.parts]
            ids = issue_ids(names, ident.get("name_to_id") or {})
            src_meta = dict(getattr(source, "metadata", None) or {}) if source else {}
            source_revision = _item_ref(src_meta)
            mesh = build_structured_mesh(
                parsed,
                ids,
                extras={
                    "source_revision": source_revision,
                    "source_node_id": str(source.id) if source else None,
                },
            )
            content_hash = hashlib.sha256(mesh.glb).hexdigest()
            span.set_attribute("structured_mesh.parts", len(names))
            prior = await _existing(pid, content_hash)
            if prior is not None:
                _record("reused")
                logger.info("structured_mesh_reused", node_id=str(prior.id), project_id=project_id)
                return _result(prior.id, prior.name, mesh, ids, source_revision, reused=True)

            wp_id = uuid4()
            filename = "structured-mesh.glb"
            try:
                key = await asyncio.to_thread(
                    write_blob, str(wp_id), filename, mesh.glb, content_type=GLB_MIME
                )
            except Exception as exc:  # noqa: BLE001 - no node without its mesh
                span.record_exception(exc)
                _record("store_failed")
                raise StructuredMeshError(f"the mesh could not be stored ({exc})") from exc

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "minio_object_key": key,
                "content_sha256": content_hash,
                "authority": RepresentationAuthority.STRUCTURED.value,
                "illustrative_label": LABEL,
                "generated_by": TOOL,
                "dimensions": "estimated",
                "size_bytes": len(mesh.glb),
                "triangle_count": mesh.triangle_count,
                "overall_mm": list(mesh.overall_mm),
                "concept_name": parsed.concept_name,
                "component_nodes": mesh.nodes,
                PART_COMPONENT_IDS_KEY: ids,
                "trigger": trigger,
                REPRESENTATION_KEY: descriptor(
                    RepresentationLevel.L1,
                    RepresentationAuthority.STRUCTURED,
                    source_revision=source_revision,
                    component_ids=[ids[n] for n in names],
                    tool=TOOL,
                    operation_id=operation_id,
                    dimensions="estimated",
                ),
            }
            if source is not None:
                metadata["source_layout_node_id"] = str(source.id)
                metadata["source_content_hash"] = source.content_hash
            if ident.get("current_decision_id"):
                metadata["assembly_decision_node_id"] = ident["current_decision_id"]
            if operation_id:
                metadata["operation_id"] = operation_id
            now = datetime.now(UTC)
            wp_name = name or f"Simple 3D parts: {parsed.concept_name}"
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=wp_name,
                    type=WorkProductType.RENDER,
                    domain=domain,
                    file_path="",
                    content_hash=content_hash,
                    format="glb",
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by=TOOL,
                    project_id=pid,
                )
            )
            if source is not None:
                await twin.add_edge(created.id, source.id, EdgeType.GENERATED_FROM)
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, str(created.id), wp_name, "render"
                    )
                except Exception as exc:  # noqa: BLE001 - the node exists
                    logger.warning("structured_mesh_project_link_failed", error=str(exc))
            _record("generated")
            logger.info(
                "structured_mesh_generated",
                node_id=str(created.id),
                project_id=project_id,
                parts=len(names),
                triangles=mesh.triangle_count,
                size_bytes=len(mesh.glb),
                source_revision=source_revision,
                trigger=trigger,
            )
            return _result(created.id, wp_name, mesh, ids, source_revision, reused=False)

    return render


def _result(
    node_id: Any,
    name: str,
    mesh: Any,
    ids: dict[str, str],
    source_revision: str | None,
    *,
    reused: bool,
) -> dict[str, Any]:
    return {
        "node_id": str(node_id),
        "name": name,
        "format": "glb",
        "level": RepresentationLevel.L1.value,
        "authority": RepresentationAuthority.STRUCTURED.value,
        "dimensions": "estimated",
        "label": LABEL,
        "source_revision": source_revision,
        "component_ids": ids,
        "nodes": mesh.nodes,
        "triangle_count": mesh.triangle_count,
        "reused": reused,
    }


__all__ = [
    "LABEL",
    "TOOL",
    "NoLayoutError",
    "StructuredMeshError",
    "make_structured_mesh_recorder",
    "set_blob_writer",
]
