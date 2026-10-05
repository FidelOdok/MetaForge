"""Geometry diff across a real SUPERSEDES chain (FORGE-301, gap G-J2).

``/nodes/{id}/diff`` (``version_service.py``) already diffs a work product's
own **metadata** revisions -- but those revisions never carry different
geometry: ``/nodes/{id}/iterate`` only merges ``metadata_updates`` into the
existing metadata, it never re-uploads a blob or changes
``metadata["minio_object_key"]``. So there is no scenario anywhere in this
codebase where two *revisions* of the same node point at different geometry.

The real "geometry actually changed" case is a **SUPERSEDES** edge between two
separate ``WORK_PRODUCT`` nodes: ``api_gateway.twin.geometry_recorder``
already links a re-committed, same-named CAD_MODEL to its predecessor this
way (``twin.commit_geometry``), and ``api_gateway.features.routes`` already
walks that same edge to diff parametric-feature *parameters* -- but nothing
diffs the actual geometry (volume / bounding box) of the two nodes' real STEP
files. This module is that missing half: resolve both nodes' real committed
blobs, describe each via a real ``freecad.describe_step_file`` call (no new
geometry algorithm -- reuses the same per-component volume/bounding-box
computation FORGE-294/273 already call), and report the delta.

Mirrors ``api_gateway.twin.manufacture_release``'s exact shape: a factory
closure over ``twin`` + an injected ``blob_stager`` + a lazily-bound
``mcp_bridge``, called from a thin REST route -- no MCP tool registration,
so this has none of ``bootstrap_tool_registry``'s signature-drift risk
(FORGE-405/298 both hit that class of bug; this capability is REST-only,
like ``manufacture_release`` itself).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.geometry_diff")

#: ``freecad.describe_step_file`` only reads STEP; a non-STEP source is
#: rejected with a clear message rather than failing confusingly downstream
#: (same convention ``manufacture_release.py`` established for
#: ``cadquery.export_geometry``).
_SUPPORTED_SOURCE_FORMATS = {"step", "stp"}


def _representative_component(components: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    """Pick the single component that best represents "the whole part".

    ``describe_step_file``'s own docstring warns a multipart STEP export
    typically includes both each named leaf part AND a top-level assembly
    compound whose volume equals the sum of the parts -- comparing by
    volume and taking the largest picks that top-level aggregate (or the
    lone solid, when there's only one), avoiding double-counting a
    same-named part's own sub-features as separate "components".
    """
    if not components:
        raise ValueError(f"{label}: STEP file has no solid components to measure")
    return max(components, key=lambda c: c["volume"])


def make_geometry_diff(twin: Any, *, blob_stager: Any, mcp_bridge: Any) -> Any:
    """Return an async ``diff(*, work_product_id) -> dict`` bound to a twin +
    blob stager + mcp_bridge (a real ``freecad.describe_step_file`` call).
    """

    async def diff(
        *, work_product_id: str, previous_work_product_id: str | None = None
    ) -> dict[str, Any]:
        """Diff against the SUPERSEDES predecessor, or against
        ``previous_work_product_id`` when given (FORGE-526: any two revisions
        of one item, ``KEY@a`` vs ``KEY@b``)."""
        with tracer.start_as_current_span("geometry.diff") as span:
            span.set_attribute("geometry_diff.work_product_id", work_product_id)

            try:
                current_id = UUID(work_product_id)
            except ValueError as exc:
                raise ValueError(
                    f"twin.geometry_diff: invalid work_product_id {work_product_id!r}"
                ) from exc

            current_wp = await twin.get_work_product(current_id)
            if current_wp is None:
                raise ValueError(f"twin.geometry_diff: no work_product {work_product_id!r}")

            if previous_work_product_id:
                try:
                    previous_id = UUID(previous_work_product_id)
                except ValueError as exc:
                    raise ValueError(
                        "twin.geometry_diff: invalid previous_work_product_id "
                        f"{previous_work_product_id!r}"
                    ) from exc
            else:
                edges = await twin.get_edges(
                    current_id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
                )
                if not edges:
                    raise LookupError(
                        f"work product {work_product_id} has no prior version (no SUPERSEDES edge)"
                    )
                previous_id = edges[0].target_id
            previous_wp = await twin.get_work_product(previous_id)
            if previous_wp is None:
                raise LookupError("superseded work product no longer exists")

            for label, wp in (("current", current_wp), ("previous", previous_wp)):
                fmt = (wp.format or "").lower()
                if fmt not in _SUPPORTED_SOURCE_FORMATS:
                    raise ValueError(
                        f"twin.geometry_diff: {label} work product has format {wp.format!r}, "
                        f"expected a STEP file ({'/'.join(sorted(_SUPPORTED_SOURCE_FORMATS))}) -- "
                        "freecad.describe_step_file only reads STEP geometry"
                    )

            current_staged = await blob_stager(str(current_id))
            previous_staged = await blob_stager(str(previous_id))

            current_desc = await mcp_bridge.invoke(
                "freecad.describe_step_file", {"input_file": current_staged["file_path"]}
            )
            previous_desc = await mcp_bridge.invoke(
                "freecad.describe_step_file", {"input_file": previous_staged["file_path"]}
            )

            current_component = _representative_component(
                current_desc.get("components", []), label="current"
            )
            previous_component = _representative_component(
                previous_desc.get("components", []), label="previous"
            )

            volume_delta = current_component["volume"] - previous_component["volume"]
            area_delta = current_component["area"] - previous_component["area"]

            logger.info(
                "geometry_diff_computed",
                work_product_id=work_product_id,
                previous_work_product_id=str(previous_id),
                current_volume=current_component["volume"],
                previous_volume=previous_component["volume"],
                volume_delta=volume_delta,
            )
            span.set_attribute("geometry_diff.volume_delta_mm3", volume_delta)

            return {
                "current_work_product_id": work_product_id,
                "previous_work_product_id": str(previous_id),
                "current_volume_mm3": current_component["volume"],
                "previous_volume_mm3": previous_component["volume"],
                "volume_delta_mm3": round(volume_delta, 2),
                "current_area_mm2": current_component["area"],
                "previous_area_mm2": previous_component["area"],
                "area_delta_mm2": round(area_delta, 2),
                "current_bounding_box": current_component["bounding_box"],
                "previous_bounding_box": previous_component["bounding_box"],
            }

    return diff
