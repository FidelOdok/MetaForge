"""High-level AAS export orchestrator.

Queries the Digital Twin graph, maps nodes to AAS submodels, and packages
the result as an AASX archive. This is the primary public API for AAS export.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.aas.mapper import AASMapper
from twin_core.aas.models import AASEnvironment
from twin_core.aas.packager import AASXPackager
from twin_core.graph_engine import GraphEngine
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.aas.exporter")


class AASExporter:
    """Orchestrates the full AAS export pipeline: query -> map -> package.

    Args:
        graph: The graph engine to query for the design twin.
        asset_id: The globalAssetId for the exported AAS shell.
        asset_name: A human-readable name for the asset.
    """

    def __init__(
        self,
        graph: GraphEngine,
        asset_id: str,
        asset_name: str,
    ) -> None:
        self._graph = graph
        self._asset_id = asset_id
        self._asset_name = asset_name
        self._mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        self._packager = AASXPackager()

    async def export_to_bytes(
        self,
        root_id: UUID,
        depth: int = 3,
        edge_types: list[EdgeType] | None = None,
    ) -> bytes:
        """Export a graph subset as an in-memory AASX archive.

        Args:
            root_id: Root node ID for the subgraph traversal.
            depth: Traversal depth from root node.
            edge_types: Optional list of edge types to follow during traversal.

        Returns:
            Raw bytes of the AASX ZIP archive.
        """
        with tracer.start_as_current_span("aas.export_to_bytes") as span:
            span.set_attribute("aas.root_id", str(root_id))
            span.set_attribute("aas.depth", depth)
            span.set_attribute("aas.asset_id", self._asset_id)

            logger.info(
                "aas_export_started",
                root_id=str(root_id),
                depth=depth,
                asset_id=self._asset_id,
            )

            try:
                # Step 1: Query graph for subgraph
                subgraph = await self._graph.get_subgraph(
                    root_id=root_id,
                    depth=depth,
                    edge_types=edge_types,
                )

                logger.info(
                    "aas_subgraph_retrieved",
                    node_count=len(subgraph.nodes),
                    edge_count=len(subgraph.edges),
                )

                # Step 2: Map to AAS environment
                environment = self._mapper.map_subgraph(subgraph)

                # Step 3: Package as AASX
                aasx_bytes = self._packager.package_to_bytes(environment)

                logger.info(
                    "aas_export_complete",
                    size_bytes=len(aasx_bytes),
                    asset_id=self._asset_id,
                )

                return aasx_bytes

            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "aas_export_failed",
                    root_id=str(root_id),
                    error=str(exc),
                )
                raise

    async def export_to_file(
        self,
        root_id: UUID,
        output_path: str | Path,
        depth: int = 3,
        edge_types: list[EdgeType] | None = None,
    ) -> Path:
        """Export a graph subset to an AASX file on disk.

        Args:
            root_id: Root node ID for the subgraph traversal.
            output_path: File path for the output .aasx file.
            depth: Traversal depth from root node.
            edge_types: Optional list of edge types to follow during traversal.

        Returns:
            The resolved Path of the written file.
        """
        with tracer.start_as_current_span("aas.export_to_file") as span:
            output = Path(output_path)
            span.set_attribute("aas.output_path", str(output))

            aasx_bytes = await self.export_to_bytes(
                root_id=root_id,
                depth=depth,
                edge_types=edge_types,
            )

            output.write_bytes(aasx_bytes)

            logger.info(
                "aas_file_written",
                path=str(output),
                size_bytes=len(aasx_bytes),
            )

            return output

    async def export_environment(
        self,
        root_id: UUID,
        depth: int = 3,
        edge_types: list[EdgeType] | None = None,
    ) -> AASEnvironment:
        """Export a graph subset as an AASEnvironment (without packaging).

        Useful for inspection or further processing before packaging.

        Args:
            root_id: Root node ID for the subgraph traversal.
            depth: Traversal depth from root node.
            edge_types: Optional list of edge types to follow during traversal.

        Returns:
            The mapped AASEnvironment.
        """
        with tracer.start_as_current_span("aas.export_environment") as span:
            span.set_attribute("aas.root_id", str(root_id))

            subgraph = await self._graph.get_subgraph(
                root_id=root_id,
                depth=depth,
                edge_types=edge_types,
            )

            return self._mapper.map_subgraph(subgraph)
