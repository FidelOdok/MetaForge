"""Generic text/document work-product recorder (MET-10).

The geometry recorder persists STEP blobs and the BOM recorder persists CSV; the
firmware phase needs the same loadable-artifact treatment for a pinmap and a
firmware source scaffold. Rather than a third bespoke recorder, this one persists
*any* text artifact as a loadable work product of a caller-chosen type, mirroring
:func:`make_geometry_recorder`:

1. store the text blob in MinIO (graceful — node still created on failure),
2. create a validated WorkProduct with ``content_hash`` +
   ``metadata["minio_object_key"]`` so it is loadable/viewable like any artifact,
3. link it to its project so it shows on the Projects page.

FORGE-532: a ``simulation_result`` can also carry its 3D result field (the
gzipped ``metaforge.sim_field`` payload ``calculix.run_fea``/``run_thermal``
build). It is stored as a SECOND blob on the same node
(``<name>.field.json.gz``) and described by flat ``field_*`` metadata
(``field_object_key``, ``field_content_hash``, ...) so it never displaces
the summary JSON as the node's primary file. The record also pins what
was analysed: ``analysed_geometry`` (node id, revision, content hash),
the ``load_case_spec`` and the ``fixtures``.

FORGE-528: a ``prd`` is the one exception. Requirement values live in the
constraint set, so a prd write records only the prose, as the next revision
of the project's prd item, and warns about values the constraint set lacks
(``api_gateway/twin/requirements_home.py``). Every caller of this recorder
gets that behaviour, the MCP tool and the requirements phase alike.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.document_recorder")

_CONTENT_TYPE = {
    "csv": "text/csv",
    "md": "text/markdown",
    "txt": "text/plain",
    "c": "text/x-csrc",
    "h": "text/x-chdr",
    "json": "application/json",
    # FORGE-241: robot_description formats.
    "urdf": "application/xml",
    "xacro": "application/xml",
    "sdf": "application/xml",
    "usd": "application/octet-stream",
    "usda": "text/plain",
}


#: Format tag every accepted result-field payload must carry (FORGE-532).
SIM_FIELD_FORMAT = "metaforge.sim_field"
#: Hard ceiling on a stored field blob. The calculix builder caps its own
#: output well below this; anything bigger did not come from it.
SIM_FIELD_MAX_BYTES = 8 * 1024 * 1024

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _record_field_metric(outcome: str) -> None:
    try:
        _collector().record_sim_field_store(outcome)
    except Exception:  # noqa: BLE001 - metrics must never break a record
        pass


def decode_sim_field(blob: bytes) -> dict[str, Any]:
    """Inflate + validate a ``metaforge.sim_field`` payload.

    Raises ``ValueError`` for anything that is not one, so a caller handing
    the wrong file gets told at record time rather than at render time.
    """
    if len(blob) > SIM_FIELD_MAX_BYTES:
        raise ValueError(
            f"result field is {len(blob)} bytes, over the {SIM_FIELD_MAX_BYTES}-byte cap"
        )
    try:
        payload = json.loads(gzip.decompress(blob))
    except (OSError, EOFError, ValueError) as exc:
        raise ValueError(f"result field is not gzipped JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != SIM_FIELD_FORMAT:
        raise ValueError(
            f"result field is not a {SIM_FIELD_FORMAT} payload (pass the 'field.file' "
            "calculix.run_fea/run_thermal returned)"
        )
    return payload


def _markers_of(payload: dict[str, Any], kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for marker in payload.get("markers") or []:
        if isinstance(marker, dict) and marker.get("kind") in kinds:
            out.append(
                {
                    k: marker[k]
                    for k in ("kind", "label", "position", "vector", "value", "unit", "node_count")
                    if k in marker
                }
            )
    return out


async def _analysed_geometry(twin: Any, node_id: str, revision: Any | None) -> dict[str, Any]:
    """Pin the geometry a result analysed: id, revision, content hash.

    The hash is what lets a later check tell the result is stale (FORGE-527):
    a new geometry revision is a new node with a different hash. Best-effort
    lookup; an unknown id is still recorded, as given.
    """
    from uuid import UUID

    pinned: dict[str, Any] = {"node_id": node_id}
    if revision is not None:
        pinned["revision"] = revision
    try:
        geometry = await twin.get_work_product(UUID(node_id))
    except Exception as exc:  # noqa: BLE001 — provenance lookup is best-effort
        logger.warning("analysed_geometry_lookup_failed", node_id=node_id, error=str(exc))
        return pinned
    if geometry is not None:
        pinned["name"] = geometry.name
        if geometry.content_hash:
            pinned["content_hash"] = geometry.content_hash
        if "revision" not in pinned:
            md_revision = (geometry.metadata or {}).get("revision")
            if md_revision is not None:
                pinned["revision"] = md_revision
    return pinned


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "document")[:60]


def make_document_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` that persists a loadable text work product."""

    async def record_raw(
        *,
        content: str,
        name: str,
        wp_type: Any,
        domain: str,
        fmt: str,
        link_type: str,
        source_tool: str,
        session_id: str | None = None,
        project_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
        source_part_node_ids: list[str] | None = None,
        source_edge_type: str = "parent_of",
        evidence_node_id: str | None = None,
        field_blob: bytes | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from uuid import UUID

        from twin_core.models.enums import EdgeType
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("document recorder: 'name' is required (non-empty string)")
        if not content:
            raise ValueError("document recorder: 'content' is required (non-empty)")

        # FORGE-532: validate the field BEFORE creating anything, so a wrong
        # file is a clean error rather than a half-recorded result.
        field_payload: dict[str, Any] | None = None
        if field_blob is not None:
            try:
                field_payload = decode_sim_field(field_blob)
            except ValueError:
                _record_field_metric("invalid")
                raise

        blob = content.encode("utf-8")
        ext = fmt.lower().lstrip(".") or "txt"
        with tracer.start_as_current_span("twin.record_document") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(blob).hexdigest()
            filename = f"{_slug(name)}.{ext}"
            span.set_attribute("document.name", name)
            span.set_attribute("document.type", str(getattr(wp_type, "value", wp_type)))
            span.set_attribute("document.size_bytes", len(blob))

            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id),
                    filename,
                    blob,
                    content_type=_CONTENT_TYPE.get(ext, "application/octet-stream"),
                )
            except Exception as exc:  # noqa: BLE001 — degrade like the other recorders
                logger.warning("document_blob_store_skipped", name=name, error=str(exc))

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "content_sha256": content_hash,
                "authored_by": source_tool,
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key
            if session_id:
                metadata["session_id"] = session_id
            if extra_metadata:
                metadata.update(extra_metadata)

            source_ids = list(source_part_node_ids or [])
            if analysis or field_payload is not None:
                analysis = analysis or {}
                geometry_id = analysis.get("geometry_node_id")
                if isinstance(geometry_id, str) and geometry_id:
                    pinned = await _analysed_geometry(
                        twin, geometry_id, analysis.get("geometry_revision")
                    )
                    metadata["analysed_geometry"] = pinned
                    # Flat copies: the node's scalar properties carry them.
                    metadata["analysed_geometry_node_id"] = geometry_id
                    if "revision" in pinned:
                        metadata["analysed_geometry_revision"] = pinned["revision"]
                    if geometry_id not in source_ids:
                        source_ids.append(geometry_id)
                if isinstance(analysis.get("load_case_spec"), dict):
                    metadata["load_case_spec"] = analysis["load_case_spec"]
                fixtures = analysis.get("fixtures")
                if isinstance(fixtures, list):
                    metadata["fixtures"] = fixtures
                elif field_payload is not None:
                    derived = _markers_of(field_payload, ("fixture", "sink"))
                    if derived:
                        metadata["fixtures"] = derived
                if field_payload is not None and "loads" not in metadata:
                    loads = _markers_of(field_payload, ("load", "heat_source"))
                    if loads:
                        metadata["loads"] = loads

            if field_blob is not None and field_payload is not None:
                field_hash = hashlib.sha256(field_blob).hexdigest()
                field_key: str | None = None
                try:
                    from digital_twin.storage.work_product_blobs import (
                        store_work_product_blob as _store_field,
                    )

                    field_key = _store_field(
                        str(wp_id),
                        f"{_slug(name)}.field.json.gz",
                        field_blob,
                        content_type="application/gzip",
                    )
                except Exception as exc:  # noqa: BLE001 — the summary still records
                    logger.warning("sim_field_blob_store_failed", name=name, error=str(exc))
                    metadata["field_store_error"] = str(exc)[:200]
                _record_field_metric("stored" if field_key else "failed")
                span.set_attribute("document.field_stored", bool(field_key))
                metadata["field_stored"] = bool(field_key)
                metadata["field_content_hash"] = field_hash
                metadata["field_size_bytes"] = len(field_blob)
                metadata["field_format"] = f"{SIM_FIELD_FORMAT}/{field_payload.get('version', 1)}"
                metadata["field_quantities"] = sorted((field_payload.get("fields") or {}).keys())
                metadata["field_analysis_type"] = field_payload.get("analysis_type")
                metadata["field_ranges"] = {
                    key: {"min": f.get("min"), "max": f.get("max"), "unit": f.get("unit")}
                    for key, f in (field_payload.get("fields") or {}).items()
                    if isinstance(f, dict)
                }
                if field_key:
                    metadata["field_object_key"] = field_key

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=wp_type,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format=ext,
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=source_tool,
                project_id=project_id,
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            # FORGE-241: e.g. a robot_description derived from one or more
            # cad_model parts -- mirrors robot_description_recorder.py's
            # PARENT_OF precedent so the twin can answer "which document
            # derives from this part" (best-effort per edge: one bad
            # node_id must not block the whole commit). FORGE-246:
            # source_edge_type is a plain string, not an EdgeType member --
            # tool_registry (layer 3) may not import twin_core (layer 4+),
            # so the adapter can only hand this recorder a name, never an
            # EdgeType instance, to pick a more accurate relation than the
            # default (e.g. "derives_from" for a simulation_result's real
            # geometric dependency on its source cad_model).
            edge_type = EdgeType(source_edge_type)
            edge_failures = 0
            for source_id in source_ids:
                try:
                    await twin.add_edge(created.id, source_id, edge_type)
                except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                    edge_failures += 1
                    logger.warning(
                        "document_source_edge_failed",
                        node_id=node_id,
                        source_id=source_id,
                        error=str(exc),
                    )

            # FORGE-246: e.g. a simulation_result the agent already recorded
            # an evidence EngineeringEntity for (twin.record_evidence) --
            # link the evidence claim back to the real, structured artifact
            # it's about, so the twin can answer "what result backs this
            # evidence" instead of only having numbers restated as text on
            # the evidence node itself.
            evidence_edge_failed = False
            if evidence_node_id:
                try:
                    await twin.add_edge(UUID(evidence_node_id), created.id, EdgeType.GENERATED_FROM)
                except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                    evidence_edge_failed = True
                    logger.warning(
                        "document_evidence_edge_failed",
                        node_id=node_id,
                        evidence_node_id=evidence_node_id,
                        error=str(exc),
                    )

            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, name, link_type)
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("document_project_link_failed", error=str(exc))

            logger.info(
                "document_recorded",
                node_id=node_id,
                wp_type=str(getattr(wp_type, "value", wp_type)),
                project_id=project_id,
                linked=linked,
                size_bytes=len(blob),
                edge_failures=edge_failures,
                evidence_edge_failed=evidence_edge_failed,
                field_stored=metadata.get("field_stored"),
            )
            out: dict[str, Any] = {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "size_bytes": len(blob),
                "project_linked": linked,
            }
            if field_payload is not None:
                out["field_stored"] = bool(metadata.get("field_stored"))
                out["field_object_key"] = metadata.get("field_object_key")
                out["field_content_hash"] = metadata.get("field_content_hash")
            return out

    async def record(**kwargs: Any) -> dict[str, Any]:
        wp_type = kwargs.get("wp_type")
        if str(getattr(wp_type, "value", wp_type)) == "prd":
            from api_gateway.twin.requirements_home import record_prd_prose

            for required in ("name", "content"):
                if not kwargs.get(required):
                    raise ValueError(f"document recorder: '{required}' is required (non-empty)")
            return await record_prd_prose(
                twin,
                record_raw,
                content=kwargs.pop("content"),
                name=kwargs.pop("name"),
                project_id=kwargs.pop("project_id", None),
                extra_metadata=kwargs.pop("extra_metadata", None),
                **kwargs,
            )
        return await record_raw(**kwargs)

    return record
