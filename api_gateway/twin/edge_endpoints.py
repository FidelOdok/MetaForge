"""Name the far end of a twin edge (FORGE-704).

``GET /v1/twin/relationships`` lists the edges of the work products. Many of
those edges run to something that is not a work product: a constraint a work
product satisfies, a baseline it belongs to, a BOM item it contains, an
engineering entity it is supported by. The dashboard can only draw an edge whose
two ends it can name, so each such end is resolved here to a kind and a short
name.

An end that cannot be read is reported as ``unknown`` with its short id, never
left out: leaving it out is how 64% of the edges on the sandbox went missing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin.schemas import TwinEndpointResponse

logger = structlog.get_logger(__name__)

#: Fields that name a node, most specific first. The node models do not share
#: one: a constraint and a baseline have ``name``, an engineering entity a
#: ``title`` or a ``statement``, a BOM item a ``part_number``.
_NAME_FIELDS = ("name", "title", "statement", "part_number", "description")
_MAX_NAME = 80
#: A project's edges end at tens to low hundreds of distinct nodes; this only
#: stops a pathological graph from turning one request into thousands of reads.
MAX_ENDPOINTS = 2000


def summarise(node: Any, node_id: str) -> TwinEndpointResponse:
    """Kind and name of a node, tolerant of whichever fields it has."""
    # Graph nodes carry their discriminator as ``node_type``; ``type`` on a work
    # product is its own WorkProductType, a different thing.
    kind = str(getattr(node, "node_type", "") or "unknown")
    if kind == "engineering_entity":
        # The entity type (requirement, intent, risk...) says more than the label.
        kind = str(getattr(node, "entity_type", "") or kind)
    for field in _NAME_FIELDS:
        value = getattr(node, field, None)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            if len(text) > _MAX_NAME:
                text = text[: _MAX_NAME - 1] + "…"
            return TwinEndpointResponse(kind=kind, name=text)
    return TwinEndpointResponse(kind=kind, name=node_id[:8])


async def resolve_endpoints(
    twin: Any, endpoint_ids: Iterable[str]
) -> dict[str, TwinEndpointResponse]:
    """Resolve each id to its kind and name. Unreadable ones become ``unknown``."""
    out: dict[str, TwinEndpointResponse] = {}
    unresolved = 0
    for i, node_id in enumerate(endpoint_ids):
        if i >= MAX_ENDPOINTS:
            unresolved += 1
            out[node_id] = TwinEndpointResponse(kind="unknown", name=node_id[:8])
            continue
        try:
            node = await twin.graph.get_node(UUID(node_id))
        except Exception:  # noqa: BLE001 - one unreadable node must not hide the rest
            node = None
        if node is None:
            unresolved += 1
            out[node_id] = TwinEndpointResponse(kind="unknown", name=node_id[:8])
        else:
            out[node_id] = summarise(node, node_id)
    if unresolved:
        logger.warning("twin_edge_endpoints_unresolved", count=unresolved, total=len(out))
    return out
