"""Helpers for the Twin MCP adapter (MET-382).

Two responsibilities:

1. **Mutation detection** for ``twin.query_cypher`` — re-exported from
   ``mcp_core.cypher``, where it moved so the approval gate can use it.
2. **Subgraph serialisation** — flatten ``SubGraph`` (nodes + edges)
   into a JSON-friendly dict the harness can read without importing
   ``twin_core`` types.

Layer-2 invariant: this module imports only stdlib + pydantic + the
existing twin_core types. No reach upward.
"""

from __future__ import annotations

from typing import Any

from mcp_core.cypher import detect_mutations

# Mutation detection moved to ``mcp_core.cypher`` (FORGE-407): the approval
# gate needs it to classify a ``twin.query_cypher`` call per query rather than
# per tool, and ``mcp_core`` cannot import this layer. Re-exported here so
# existing callers keep working and there stays exactly one implementation.


def serialise_subgraph(subgraph: Any) -> dict[str, Any]:
    """Flatten a ``twin_core.models.relationship.SubGraph`` for the wire.

    Each node/edge is dumped individually so subclass fields survive
    (``SubGraph.nodes: list[NodeBase]`` would otherwise erase
    ``WorkProduct.name`` etc. via parent-type narrowing in pydantic).
    """
    if subgraph is None:
        return {"nodes": [], "edges": [], "root_id": None, "depth": 0}

    nodes = [serialise_node(n) for n in getattr(subgraph, "nodes", []) or []]
    edges = [serialise_node(e) for e in getattr(subgraph, "edges", []) or []]
    root_id = getattr(subgraph, "root_id", None)
    depth = getattr(subgraph, "depth", 0)
    return {
        "nodes": nodes,
        "edges": edges,
        "root_id": str(root_id) if root_id is not None else None,
        "depth": depth,
    }


def serialise_node(node: Any) -> dict[str, Any]:
    """Same shape transform for a single node."""
    if node is None:
        return {}
    if hasattr(node, "model_dump"):
        dumped: dict[str, Any] = node.model_dump(mode="json")
        return dumped
    return dict(node)


def serialise_violation(v: Any) -> dict[str, Any]:
    """Standard wire shape for a ``ConstraintViolation``."""
    if hasattr(v, "model_dump"):
        dumped: dict[str, Any] = v.model_dump(mode="json")
        return dumped
    return dict(v)


__all__ = [
    "detect_mutations",
    "serialise_node",
    "serialise_subgraph",
    "serialise_violation",
]
