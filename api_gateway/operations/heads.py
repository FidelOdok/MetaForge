"""The current head of an operation's target (FORGE-788, DI-003, PERF-004).

An operation that names a ``target_key`` and the ``base_revision`` it starts
from is checked against the target's head when its result arrives. The
target is a twin item (``CAD-BRACKET``, or ``CAD-BRACKET@3`` with the pin
ignored), and its head is the item's ``head_revision``: the same number the
twin's own conflict checks compare (``KEY@n`` pins and change-set bases in
``twin_core.items``). Nothing new decides what "current" means here.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.heads")

__all__ = ["item_head_revision", "make_item_head_resolver"]


async def item_head_revision(twin: Any, project_id: str, target_key: str) -> str | None:
    """The head revision of item ``target_key`` in ``project_id``, as text.

    ``None`` when the twin holds no items, the key is unknown in the project,
    or the item has no head yet: there is then nothing a result could
    overwrite, and nothing to call it stale against.
    """
    from twin_core.items import find_item, parse_item_ref, supports_items

    if twin is None or not supports_items(twin):
        return None
    try:
        key, _pin = parse_item_ref(target_key)
    except ValueError:
        return None  # not an item key: nothing in the twin to compare with
    with tracer.start_as_current_span("operations.item_head") as span:
        span.set_attribute("target_key", key)
        try:
            item = await find_item(twin, key, project_id)
        except ValueError:
            # A project id that is not a UUID holds no items.
            return None
        if item is None or item.head_node_id is None:
            return None
        span.set_attribute("head_revision", item.head_revision)
        return str(item.head_revision)


def make_item_head_resolver(twin_getter: Any) -> Any:
    """A ``(project_id, target_key) -> head`` resolver over the twin ``twin_getter()`` gives."""

    async def resolve(project_id: str, target_key: str) -> str | None:
        return await item_head_revision(twin_getter(), project_id, target_key)

    return resolve
