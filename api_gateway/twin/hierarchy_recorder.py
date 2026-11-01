"""Product-hierarchy write path + rollup bridge (FORGE-260, gap G-B1).

``tool_registry`` (layer 3) may not import ``twin_core`` (layer 4+) -- the
same rule ``document_recorder.py``/``robot_description_recorder.py`` are
built around. Constructing a real ``HierarchyNode`` and reading a
``HierarchyRollup`` both need real ``twin_core`` types, so (like every
other recorder in this codebase) that construction happens here, in
``api_gateway``, and the twin adapter only ever receives the two plain
async callables built below.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.hierarchy_recorder")


def make_hierarchy_node_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` that creates one HierarchyNode and
    its structural edges (FORGE-260)."""

    async def record(
        *,
        name: str,
        kind: str,
        project_id: str | None = None,
        parent_id: str | None = None,
        quantity: float = 1,
        placement: dict[str, Any] | None = None,
        realized_by_node_id: str | None = None,
        instance_of_node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType
        from twin_core.models.hierarchy_node import HierarchyNode

        if not name or not isinstance(name, str):
            raise ValueError("hierarchy node recorder: 'name' is required (non-empty string)")
        if kind not in ("product", "system", "subsystem", "assembly"):
            raise ValueError(
                f"hierarchy node recorder: 'kind' must be one of "
                f"product/system/subsystem/assembly, got {kind!r}"
            )

        with tracer.start_as_current_span("twin.record_hierarchy_node") as span:
            span.set_attribute("hierarchy_node.kind", kind)
            node = HierarchyNode(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                project_id=UUID(project_id) if project_id else None,
                created_by="twin.record_hierarchy_node",
                metadata=metadata or {},
            )
            created = await twin.create_hierarchy_node(node)
            node_id = created.id
            span.set_attribute("hierarchy_node.node_id", str(node_id))

            parent_linked = False
            if parent_id:
                try:
                    edge_metadata: dict[str, Any] = {"quantity": quantity}
                    if placement:
                        edge_metadata["placement"] = placement
                    await twin.add_edge(UUID(parent_id), node_id, EdgeType.CONTAINS, edge_metadata)
                    parent_linked = True
                except Exception as exc:  # noqa: BLE001 — the node itself still exists
                    logger.warning(
                        "hierarchy_node_parent_link_failed",
                        node_id=str(node_id),
                        parent_id=parent_id,
                        error=str(exc),
                    )

            realized_by_linked = False
            if realized_by_node_id:
                try:
                    await twin.add_edge(node_id, UUID(realized_by_node_id), EdgeType.REALIZED_BY)
                    realized_by_linked = True
                except Exception as exc:  # noqa: BLE001 — best-effort provenance edge
                    logger.warning(
                        "hierarchy_node_realized_by_failed",
                        node_id=str(node_id),
                        target_id=realized_by_node_id,
                        error=str(exc),
                    )

            instance_of_linked = False
            if instance_of_node_id:
                try:
                    await twin.add_edge(node_id, UUID(instance_of_node_id), EdgeType.INSTANCE_OF)
                    instance_of_linked = True
                except Exception as exc:  # noqa: BLE001 — best-effort provenance edge
                    logger.warning(
                        "hierarchy_node_instance_of_failed",
                        node_id=str(node_id),
                        target_id=instance_of_node_id,
                        error=str(exc),
                    )

            logger.info(
                "hierarchy_node_recorded",
                node_id=str(node_id),
                kind=kind,
                project_id=project_id,
                parent_linked=parent_linked,
                realized_by_linked=realized_by_linked,
                instance_of_linked=instance_of_linked,
            )
            return {
                "node_id": str(node_id),
                "parent_linked": parent_linked,
                "realized_by_linked": realized_by_linked,
                "instance_of_linked": instance_of_linked,
            }

    return record


async def _replace_outgoing_edge(twin: Any, node_id: UUID, edge_type: Any, target_id: UUID) -> None:
    """Remove any existing outgoing edge of ``edge_type`` from ``node_id``
    before adding the new one -- ``realize`` always REPLACES a node's
    REALIZED_BY/INSTANCE_OF target, it never accumulates a second one."""
    existing = await twin.get_edges(node_id, direction="outgoing", edge_type=edge_type)
    for edge in existing:
        await twin.remove_edge(node_id, edge.target_id, edge_type)
    await twin.add_edge(node_id, target_id, edge_type)


def make_hierarchy_geometry_linker(twin: Any) -> Any:
    """Return an async ``realize(...)`` that attaches or REPLACES a
    HierarchyNode's real geometry -- ``REALIZED_BY`` (a fabricated part's
    CAD_MODEL work product) and/or ``INSTANCE_OF`` (a COTS component's
    BOMItem) -- after the node already exists (FORGE-266, gap G-C2).

    ``make_hierarchy_node_recorder`` above only ever ADDS these two edges
    once, at node-creation time -- there was no way to attach or swap a
    node's geometry afterward, which is exactly what "replace placeholder
    with part" needs. This is the missing half, not a new edge type: it
    reuses the same ``REALIZED_BY``/``INSTANCE_OF`` semantics
    ``twin_core.consistency.hierarchical_bom``'s EBOM derivation already
    reads, so a node realized here shows up there with zero further
    changes.
    """

    async def realize(
        *,
        hierarchy_node_id: str,
        work_product_id: str | None = None,
        bom_item_id: str | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.bom_item import BOMItem
        from twin_core.models.enums import EdgeType

        if not work_product_id and not bom_item_id:
            raise ValueError(
                "hierarchy geometry linker: at least one of 'work_product_id' or "
                "'bom_item_id' is required"
            )

        node_id = UUID(hierarchy_node_id)
        node = await twin.get_hierarchy_node(node_id)
        if node is None:
            raise ValueError(f"hierarchy geometry linker: no hierarchy node {hierarchy_node_id!r}")

        with tracer.start_as_current_span("twin.realize_hierarchy_node") as span:
            span.set_attribute("hierarchy_node.node_id", hierarchy_node_id)

            realized_by_id: str | None = None
            if work_product_id is not None:
                wp = await twin.get_work_product(UUID(work_product_id))
                if wp is None:
                    raise ValueError(
                        f"hierarchy geometry linker: no work product {work_product_id!r}"
                    )
                await _replace_outgoing_edge(
                    twin, node_id, EdgeType.REALIZED_BY, UUID(work_product_id)
                )
                realized_by_id = work_product_id

            instance_of_id: str | None = None
            if bom_item_id is not None:
                bom_node = await twin.graph.get_node(UUID(bom_item_id))
                if not isinstance(bom_node, BOMItem):
                    raise ValueError(f"hierarchy geometry linker: no BOMItem {bom_item_id!r}")
                await _replace_outgoing_edge(twin, node_id, EdgeType.INSTANCE_OF, UUID(bom_item_id))
                instance_of_id = bom_item_id

            logger.info(
                "hierarchy_node_realized",
                node_id=hierarchy_node_id,
                realized_by=realized_by_id,
                instance_of=instance_of_id,
            )
            return {
                "node_id": hierarchy_node_id,
                "realized_by_work_product_id": realized_by_id,
                "instance_of_bom_item_id": instance_of_id,
            }

    return realize


def make_hierarchy_rollup_fn(twin: Any) -> Any:
    """Return an async ``rollup(root_id: str) -> dict`` wrapping
    ``compute_hierarchy_rollup`` (FORGE-260) -- returns a plain dict, never
    the ``HierarchyRollup`` pydantic instance itself, so the twin adapter
    (layer 3) never needs to import twin_core to read the result."""

    async def rollup(root_id: str) -> dict[str, Any]:
        from twin_core.consistency.hierarchy_rollup import compute_hierarchy_rollup

        with tracer.start_as_current_span("twin.compute_hierarchy_rollup") as span:
            span.set_attribute("hierarchy_rollup.root_id", root_id)
            result = await compute_hierarchy_rollup(twin, UUID(root_id))
            span.set_attribute("hierarchy_rollup.mass_kg", result.mass_kg)
            span.set_attribute("hierarchy_rollup.node_count", result.node_count)
            return result.model_dump(mode="json")

    return rollup
