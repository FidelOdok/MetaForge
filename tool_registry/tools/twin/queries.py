"""Helpers for the Twin MCP adapter (MET-382).

Two responsibilities:

1. **Mutation detection** for ``twin.query_cypher`` — reject Cypher
   that would change graph state unless the caller explicitly opts in.
2. **Subgraph serialisation** — flatten ``SubGraph`` (nodes + edges)
   into a JSON-friendly dict the harness can read without importing
   ``twin_core`` types.

Layer-2 invariant: this module imports only stdlib + pydantic + the
existing twin_core types. No reach upward.
"""

from __future__ import annotations

import re
from typing import Any

# Cypher mutation keywords. The check is case-insensitive and looks
# for the word as a token (whitespace / start-of-line on both sides) so
# legitimate property names containing the substring don't trip it
# (e.g. ``RETURN n.created_at`` doesn't match ``CREATE``).
_MUTATION_KEYWORDS: tuple[str, ...] = (
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "MERGE",
    "SET",
    "REMOVE",
    "FOREACH",  # only used inside mutating loops in practice
    "LOAD",  # LOAD CSV
)

# ``\b`` is fine for Cypher because it's ASCII-only.
_MUTATION_PATTERN: re.Pattern[str] = re.compile(
    r"\b(" + "|".join(_MUTATION_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def detect_mutations(cypher: str) -> list[str]:
    """Return the lowercased mutation keywords found in ``cypher``.

    Empty list = read-only. Caller decides what to do with the
    detected list (raise to reject, or log to audit).
    """
    if not cypher:
        return []
    matches = _MUTATION_PATTERN.findall(cypher)
    # Dedupe while preserving discovery order so audit logs read
    # naturally.
    seen: dict[str, None] = {}
    for m in matches:
        seen.setdefault(m.upper(), None)
    return list(seen.keys())


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
