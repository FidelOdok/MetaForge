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
