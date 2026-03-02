"""Synchronous read-only view of graph state for constraint expression evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from twin_core.models.artifact import Artifact
from twin_core.models.component import Component
from twin_core.models.enums import EdgeType, NodeType

if TYPE_CHECKING:
    from twin_core.graph_engine import GraphEngine


class ConstraintContext:
    """Synchronous read-only snapshot of graph state, exposed as ``ctx`` in expressions.

    Pre-loaded by :func:`build_context` so that ``eval()`` never needs to ``await``.
    """

    def __init__(
        self,
        artifacts_by_name: dict[str, Artifact],
        artifacts_by_id: dict[UUID, Artifact],
        all_components: list[Component],
        dependency_map: dict[UUID, list[UUID]],
    ) -> None:
        self._by_name = artifacts_by_name
        self._by_id = artifacts_by_id
        self._components = all_components
        self._dep_map = dependency_map

    def artifact(self, name: str) -> Artifact:
        """Lookup an artifact by name. Raises ``KeyError`` if not found."""
        if name not in self._by_name:
            raise KeyError(f"Artifact '{name}' not found")
        return self._by_name[name]

    def artifacts(
        self,
        domain: str | None = None,
        type: str | None = None,
    ) -> list[Artifact]:
        """Return artifacts, optionally filtered by domain and/or type."""
        result = list(self._by_id.values())
        if domain is not None:
            result = [a for a in result if a.domain == domain]
        if type is not None:
            result = [a for a in result if a.type == type]
        return result

    def components(self) -> list[Component]:
        """Return all components in the graph."""
        return list(self._components)

    def dependents(self, artifact_id: UUID) -> list[Artifact]:
        """Return artifacts that have incoming DEPENDS_ON edges to *artifact_id*."""
        dep_ids = self._dep_map.get(artifact_id, [])
        return [self._by_id[aid] for aid in dep_ids if aid in self._by_id]


async def build_context(graph: GraphEngine) -> ConstraintContext:
    """Async factory that pre-loads graph state into a synchronous ConstraintContext."""
    # Load all artifacts
    artifact_nodes = await graph.list_nodes(node_type=NodeType.ARTIFACT)
    artifacts_by_name: dict[str, Artifact] = {}
    artifacts_by_id: dict[UUID, Artifact] = {}
    for node in artifact_nodes:
        assert isinstance(node, Artifact)
        artifacts_by_name[node.name] = node
        artifacts_by_id[node.id] = node

    # Load all components
    component_nodes = await graph.list_nodes(node_type=NodeType.COMPONENT)
    all_components = [n for n in component_nodes if isinstance(n, Component)]

    # Build dependency map: target_id -> list of source_ids with DEPENDS_ON edges
    dependency_map: dict[UUID, list[UUID]] = {}
    for artifact_id in artifacts_by_id:
        incoming_edges = await graph.get_edges(
            artifact_id, direction="incoming", edge_type=EdgeType.DEPENDS_ON
        )
        if incoming_edges:
            dependency_map[artifact_id] = [e.source_id for e in incoming_edges]

    return ConstraintContext(
        artifacts_by_name=artifacts_by_name,
        artifacts_by_id=artifacts_by_id,
        all_components=all_components,
        dependency_map=dependency_map,
    )
