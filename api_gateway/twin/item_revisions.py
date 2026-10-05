"""Recorder-side glue for item revisions (FORGE-523).

Every definition recorder (geometry, constraint set, engineering entity,
BOM, component selection) does the same three things around its own node
creation; this module is that shared shape so each recorder adds a few lines,
not a copy of the resolution rules (which live in ``twin_core.items``).

The author and run come from the MCP call context when there is one
(``actor_id``, and ``run_id`` from a design-flow worker call), so no tool
grows a required argument for them.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from twin_core.items import RevisionPlan, commit_revision, plan_revision

logger = structlog.get_logger(__name__)


def _call_context() -> Any:
    try:
        from mcp_core.context import current_context
    except ImportError:  # pragma: no cover -- mcp_core ships with the gateway
        return None
    return current_context()


def revision_author(default: str) -> str:
    """The calling actor, or ``default`` (the recording tool) when unattributed."""
    ctx = _call_context()
    actor = getattr(ctx, "actor_id", None)
    if isinstance(actor, str) and actor and actor != "system:unattributed":
        return actor
    return default


def revision_run_id(explicit: str | None = None) -> str | None:
    """An explicit run id, else the design-flow run of the current MCP call."""
    if explicit:
        return explicit
    ctx = _call_context()
    run_id = getattr(ctx, "run_id", None)
    return run_id if isinstance(run_id, str) and run_id else None


async def plan_definition_revision(
    twin: Any,
    *,
    item_type: str,
    name: str,
    project_id: str | UUID | None,
    default_author: str,
    item_key: str | None = None,
    supersedes: str | None = None,
    change_reason: str | None = None,
    run_id: str | None = None,
    legacy_lookup: Any = None,
) -> RevisionPlan | None:
    """``twin_core.items.plan_revision`` with author/run filled from context."""
    return await plan_revision(
        twin,
        item_type=item_type,
        name=name,
        project_id=project_id,
        author=revision_author(default_author),
        item_key=item_key,
        supersedes=supersedes,
        change_reason=change_reason,
        run_id=revision_run_id(run_id),
        legacy_lookup=legacy_lookup,
    )


async def finish_definition_revision(
    twin: Any,
    plan: RevisionPlan | None,
    node_id: UUID,
    *,
    name: str,
    result: dict[str, Any],
    link_supersedes: bool = True,
) -> None:
    """Attach ``node_id`` to its item and add ``item_key``/``revision`` to ``result``.

    Raises ``ItemRevisionConflictError`` when a pinned ``KEY@n`` write lost a
    race to another writer (see ``twin_core.items.commit_revision``).
    """
    if plan is None:
        return
    item = await commit_revision(twin, plan, node_id, name=name, link_supersedes=link_supersedes)
    if item is None:
        result["item_warning"] = (
            f"saved, but linking it to item {plan.key} as revision {plan.revision} failed; "
            "it may show as a separate node until repaired"
        )
        return
    result.update(plan.result_fields())
    if plan.prior_node_id is not None and (
        "supersedes_node_id" not in result or plan.planned_revision is not None
    ):
        # After a renumber (another write moved the head first) the
        # predecessor is the actual head, not the one the caller planned on.
        result["supersedes_node_id"] = str(plan.prior_node_id)


def item_to_dict(item: Any) -> dict[str, Any]:
    """The JSON shape of an item, shared by the MCP tool and the REST route."""
    return {
        "key": item.key,
        "item_type": item.item_type,
        "name": item.name,
        "project_id": str(item.project_id) if item.project_id else None,
        "head_revision": item.head_revision,
        "head_node_id": str(item.head_node_id),
        "head_ref": f"{item.key}@{item.head_revision}",
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def revision_to_dict(revision: Any) -> dict[str, Any]:
    out: dict[str, Any] = revision.model_dump(mode="json")
    return out


def make_item_history_reader(twin: Any) -> Any:
    """Return the async ``read(...)`` behind ``twin.item_history`` (FORGE-523)."""
    from twin_core.items import (
        UnknownItemError,
        find_item,
        item_for_node,
        item_history,
        parse_item_ref,
    )

    async def read(
        *,
        item_key: str | None = None,
        node_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if item_key:
            key, _ = parse_item_ref(item_key)
            item = await find_item(twin, key, project_id, any_project=True)
            if item is None:
                raise UnknownItemError(f"twin.item_history: no item {key}")
        elif node_id:
            try:
                found = await item_for_node(twin, UUID(str(node_id)))
            except ValueError as exc:
                raise ValueError(f"twin.item_history: node_id {node_id!r} is not a UUID") from exc
            if found is None:
                raise UnknownItemError(
                    f"twin.item_history: node {node_id} is not a revision of any item "
                    "(records and unclassified types have no item)"
                )
            item = found[0]
        else:
            raise ValueError("twin.item_history: pass 'item_key' or 'node_id'")
        revisions = await item_history(twin, item)
        logger.info("item_history_read", item_key=item.key, revisions=len(revisions))
        return {
            "item": item_to_dict(item),
            "revisions": [revision_to_dict(r) for r in revisions],
        }

    return read
