"""Real CSG boolean-cut between two committed CAD work products (MET-612).

A human clicking Hole/Group on their own open model is not meaningfully
different from clicking Save — this bypasses the ``twin.propose_change``
HITL pipeline the same way ``twin.record_document`` does (its single-action
apply executor doesn't fit this shape either). The flow drives the same
containerized CadQuery adapter the chat/agent authoring path already uses
(via the shared ``McpBridge``), then commits the result through the geometry
recorder directly — mirroring ``api_gateway.cad.builder.build_assembly``,
which also authors over the bridge but commits via the recorder rather than
the ``twin.commit_geometry`` MCP tool (whose adapter handler does not forward
arbitrary ``extra_metadata``, which this flow needs for provenance).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.boolean_ops")

_STEP_FORMATS = {"step", "stp"}

# Beyond this relative volume difference, the cut is treated as a genuine
# subtraction rather than CadQuery's cutter-missed-the-target no-op.
_NO_OVERLAP_TOLERANCE = 0.001  # 0.1%


class BooleanOpError(Exception):
    """Base for boolean-cut failures the route maps to a specific HTTP status."""


class NodeNotFoundError(BooleanOpError):
    """A target or cutter node id does not resolve to a work product."""


class InvalidFormatError(BooleanOpError):
    """A node's format isn't STEP — the only format CadQuery's boolean op reads."""


class NoOverlapError(BooleanOpError):
    """The cutter doesn't intersect the target — nothing was committed."""


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    """Unwrap an MCP result envelope, raising on error (mirrors cad/builder.py)."""
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise BooleanOpError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


async def _get_node(twin: Any, node_id: str, role: str) -> Any:
    from uuid import UUID

    try:
        uid = UUID(node_id)
    except ValueError as exc:
        raise NodeNotFoundError(f"Invalid {role} node id: {node_id!r}") from exc
    wp = await twin.get_work_product(uid)
    if wp is None:
        raise NodeNotFoundError(f"{role.capitalize()} node not found: {node_id}")
    if (wp.format or "").lower() not in _STEP_FORMATS:
        raise InvalidFormatError(
            f"{role.capitalize()} node {node_id} is format '{wp.format}' — "
            "boolean-cut only reads STEP"
        )
    return wp


async def perform_boolean_op(
    *,
    twin: Any,
    bridge: Any,
    recorder: Any,
    target_node_id: str,
    cutter_node_id: str,
    operation: str,
    result_name: str | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Cut/union/intersect two STEP work products and commit the result.

    Returns the recorder's result dict (``node_id``, ``model_url``, ...) plus
    ``result_volume_mm3``/``result_area_mm2``. Raises ``NodeNotFoundError``,
    ``InvalidFormatError``, or ``NoOverlapError`` for the caller to map to
    404/422/409; any other exception (adapter down, boolean op crashed)
    propagates for the caller to map to 503.
    """
    from api_gateway.twin.blob_store import resolve_work_product_blob

    with tracer.start_as_current_span("twin.boolean_cut") as span:
        span.set_attribute("boolean_cut.operation", operation)
        span.set_attribute("boolean_cut.target_node_id", target_node_id)
        span.set_attribute("boolean_cut.cutter_node_id", cutter_node_id)

        target = await _get_node(twin, target_node_id, "target")
        cutter = await _get_node(twin, cutter_node_id, "cutter")

        root = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        scratch = root / "_boolean_cut" / str(uuid4())
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            target_path = scratch / "a.step"
            cutter_path = scratch / "b.step"
            result_path = scratch / "result.step"

            target_content, _ = resolve_work_product_blob(target)
            cutter_content, _ = resolve_work_product_blob(cutter)
            target_path.write_bytes(target_content)
            cutter_path.write_bytes(cutter_content)

            async def invoke(tool: str, args: dict[str, Any]) -> dict[str, Any]:
                return _data(await bridge.invoke(tool, args), tool)

            pre_props = await invoke(
                "cadquery.get_properties",
                {"input_file": str(target_path), "properties": ["volume"]},
            )
            pre_volume = pre_props.get("volume_mm3")

            result = await invoke(
                "cadquery.boolean_operation",
                {
                    "input_file_a": str(target_path),
                    "input_file_b": str(cutter_path),
                    "operation": operation,
                    "output_path": str(result_path),
                },
            )
            result_volume = result.get("result_volume")
            result_area = result.get("result_area")

            # No-overlap guard (MET-612): CadQuery's .cut() doesn't error when
            # the cutter misses the target — it just returns the target
            # unchanged. A near-identical volume means nothing was actually
            # subtracted, so reject rather than commit a duplicate-looking node.
            if (
                operation == "subtract"
                and isinstance(pre_volume, (int, float))
                and isinstance(result_volume, (int, float))
                and pre_volume > 0
                and abs(result_volume - pre_volume) / pre_volume < _NO_OVERLAP_TOLERANCE
            ):
                raise NoOverlapError("Cutter does not intersect the target — nothing was committed")

            if not result_path.exists():
                raise BooleanOpError("cadquery.boolean_operation produced no output file")

            import base64

            step_b64 = base64.b64encode(result_path.read_bytes()).decode("ascii")
            name = result_name or f"{target.name} {operation}"

            rec = await recorder(
                step_base64=step_b64,
                name=name,
                project_id=str(target.project_id) if target.project_id else None,
                domain=target.domain,
                fmt="step",
                source_tool="twin.boolean_cut",
                extra_metadata={
                    "boolean_op": operation,
                    "source_target_node_id": target_node_id,
                    "source_cutter_node_id": cutter_node_id,
                    "result_volume_mm3": result_volume,
                    "result_area_mm2": result_area,
                },
            )
            node_id = rec.get("node_id")

            # Best-effort provenance edges — log-and-continue on failure,
            # matching the existing project-link step's error handling.
            if node_id:
                from twin_core.models.enums import EdgeType

                for source_id, relation in (
                    (target_node_id, "boolean_target"),
                    (cutter_node_id, "boolean_cutter"),
                ):
                    try:
                        from uuid import UUID

                        await twin.add_edge(
                            UUID(source_id),
                            UUID(node_id),
                            EdgeType.PARENT_OF,
                            {"relation": relation, "operation": operation},
                        )
                    except Exception as exc:  # noqa: BLE001 — provenance is best-effort
                        logger.warning(
                            "boolean_cut_provenance_edge_failed",
                            source_id=source_id,
                            node_id=node_id,
                            error=str(exc),
                        )

            logger.info(
                "boolean_cut_committed",
                node_id=node_id,
                operation=operation,
                target_node_id=target_node_id,
                cutter_node_id=cutter_node_id,
                result_volume_mm3=result_volume,
            )
            return {
                **rec,
                "result_volume_mm3": result_volume,
                "result_area_mm2": result_area,
            }
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
