"""Recorder-side glue for records pinned to revisions (FORGE-527).

The record recorders (``twin.record_document`` for a ``simulation_result``,
``twin.record_decision``, ``twin.record_evidence``) do the same two things
around their own node creation: resolve the ``KEY@n`` pins before anything is
written, then link them once the node exists. The rules live in
``twin_core.consistency.record_pins``; this module only fills the run from the
MCP call context, the same way ``item_revisions`` does for definitions, so no
tool grows a required argument.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from api_gateway.twin.item_revisions import revision_run_id
from twin_core.consistency.record_pins import (
    PINS_KEY,
    STALENESS_KEY,
    ItemPin,
    PinResult,
    link_record,
    pin_metadata,
    record_pins,
    record_staleness,
    resolve_pins,
)
from twin_core.items import classify
from twin_core.items.registry import TwinTypeKind


def is_record_type(type_name: Any) -> bool:
    """True when ``type_name`` is an append-only record in the type registry."""
    spec = classify(str(getattr(type_name, "value", type_name) or ""))
    return spec is not None and spec.kind is TwinTypeKind.RECORD


async def resolve_record_pins(
    twin: Any,
    *,
    depends_on: list[str] | None = None,
    node_ids: list[Any] | None = None,
    project_id: str | UUID | None = None,
    include_constraint_sets: bool = False,
) -> list[ItemPin]:
    """The pins a record is about to be born with (raises on a bad explicit ref)."""
    return await resolve_pins(
        twin,
        refs=[str(r) for r in depends_on or [] if r],
        node_ids=list(node_ids or []),
        project_id=project_id,
        run_id=revision_run_id(),
        include_constraint_sets=include_constraint_sets,
    )


def record_view(type_name: Any, metadata: dict[str, Any] | None) -> dict[str, Any]:
    """``{"dependsOn": ..., "staleness": ...}`` for a node response.

    Empty for a node that is neither a record type nor carries pins, so a
    definition's response is unchanged.
    """
    meta = metadata or {}
    if not is_record_type(type_name) and PINS_KEY not in meta and STALENESS_KEY not in meta:
        return {}
    pins = [
        {
            "itemKey": str(p["item_key"]),
            "revision": int(p.get("revision") or 0),
            "itemRef": str(p.get("item_ref") or f"{p['item_key']}@{p.get('revision')}"),
            "itemType": str(p.get("item_type") or ""),
            "nodeId": str(p["node_id"]) if p.get("node_id") else None,
        }
        for p in record_pins(meta)
    ]
    stale_for = meta.get("stale_for")
    return {
        "dependsOn": pins,
        "staleness": {
            "status": record_staleness(meta),
            "reason": meta.get("staleness_reason") or None,
            "staleFor": stale_for if isinstance(stale_for, dict) else None,
            "supersededBy": meta.get("superseded_by") or None,
        },
    }


__all__ = [
    "ItemPin",
    "PinResult",
    "is_record_type",
    "link_record",
    "pin_metadata",
    "record_view",
    "resolve_record_pins",
]
