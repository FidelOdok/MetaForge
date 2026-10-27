"""Keep the electronics thread linked when a recorder is not told its source (FORGE-705).

The electronics thread is a chain of ``GENERATED_FROM`` edges between design
records::

    bom -> schematic -> pcb_layout -> gerber, pick_and_place, board cad_model
           schematic -> pinmap -> firmware_source

A recorder links a record to the ``source_node_ids`` it is given. A run
recorded its schematic and its 3D board without them, nothing said so, and
the dashboard showed two stages as not linked. Two helpers close that:

* :func:`infer_upstream` -- when a record names no source, the current record
  of the stage before it in the same project, if there is exactly one.
* :func:`link_thread` -- after a record, the same inference for every current
  stage of the project that has no link to the stage before it. A schematic
  recorded before its BOM is linked when the BOM arrives. A stage with more
  than one candidate cannot be inferred and comes back as unlinked, by name;
  a stage whose upstream is not recorded at all comes back as missing it
  (a firmware-only project has no schematic, and that is not a broken link).

Only a board's 3D model belongs to this chain: a ``cad_model`` is a stage
only when KiCad made it (``created_by`` ``kicad.*``), never an enclosure or a
bracket.

Every inferred edge is reported, so the caller can show what was assumed.
Both helpers are best effort: a twin that cannot answer leaves the record as
it was and says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.thread_links")

#: Each stage of the electronics thread -> the stage it is generated from.
UPSTREAM: dict[str, str] = {
    "schematic": "bom",
    "pcb_layout": "schematic",
    "gerber": "pcb_layout",
    "pick_and_place": "pcb_layout",
    "cad_model": "pcb_layout",
    "pinmap": "schematic",
    "firmware_source": "pinmap",
}


@dataclass
class ThreadLinks:
    """What :func:`link_thread` did.

    ``inferred`` are the edges it added; ``unlinked`` the stages with an
    upstream it could not choose; ``missing`` the stages whose upstream is not
    recorded in the project.
    """

    inferred: list[dict[str, str]] = field(default_factory=list)
    unlinked: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def is_thread_stage(wp_type: str, node: Any) -> bool:
    """True when ``node`` (of ``wp_type``) is a stage of the electronics thread."""
    if wp_type not in UPSTREAM:
        return False
    if wp_type != "cad_model":
        return True
    meta = dict(getattr(node, "metadata", None) or {})
    author = str(getattr(node, "created_by", "") or meta.get("authored_by") or "")
    return author.startswith("kicad.")


def _can_walk(twin: Any) -> bool:
    """A real twin can list and walk; a unit-test double that cannot is skipped."""
    return all(
        callable(getattr(twin, name, None))
        for name in ("list_work_products", "get_edges", "get_work_product", "add_edge")
    )


def _type_of(node: Any) -> str:
    t = getattr(node, "type", "")
    return str(getattr(t, "value", t))


async def current_records(twin: Any, project_id: UUID, wp_type: str) -> list[Any]:
    """The current record of each ``wp_type`` item in the project.

    For a versioned item that is its newest revision (a draft inside a run
    counts, it is what the run is working on). A record with no item counts
    unless something supersedes it. A record marked stale is left out.
    """
    from twin_core.models.enums import EdgeType, WorkProductType

    nodes = await twin.list_work_products(
        work_product_type=WorkProductType(wp_type), project_id=project_id
    )
    newest: dict[str, tuple[int, Any]] = {}
    loose: list[Any] = []
    for node in nodes:
        meta = dict(getattr(node, "metadata", None) or {})
        if str(meta.get("staleness") or "current") not in ("current", "revalidated"):
            continue
        if wp_type == "cad_model" and not is_thread_stage(wp_type, node):
            continue
        key = meta.get("item_key")
        if key:
            rev = int(meta.get("item_revision") or 0)
            if key not in newest or rev > newest[str(key)][0]:
                newest[str(key)] = (rev, node)
            continue
        superseded = await twin.get_edges(
            node.id, direction="incoming", edge_type=EdgeType.SUPERSEDES
        )
        if not superseded:
            loose.append(node)
    return [n for _, n in newest.values()] + loose


@dataclass(frozen=True)
class Upstream:
    """The answer of :func:`infer_upstream`: a node id, or why there is none."""

    node_id: str | None = None
    reason: str | None = None
    #: True when candidates exist but more than one: a real unlinked stage.
    ambiguous: bool = False


async def infer_upstream(twin: Any, project_id: str | UUID | None, wp_type: str) -> Upstream:
    """The one current record of the stage before ``wp_type``, or why there is none."""
    upstream = UPSTREAM.get(wp_type)
    if upstream is None or not _can_walk(twin):
        return Upstream()
    if not project_id:
        return Upstream(reason=f"no source given and no project to find its {upstream} in")
    try:
        found = await current_records(twin, UUID(str(project_id)), upstream)
    except Exception as exc:  # noqa: BLE001 -- inference is best effort
        logger.warning("thread_upstream_lookup_failed", type=wp_type, error=str(exc))
        return Upstream(reason=f"could not look up the project's {upstream}: {exc}")
    if len(found) == 1:
        return Upstream(node_id=str(found[0].id))
    if not found:
        return Upstream(reason=f"{wp_type} is not linked: the project has no current {upstream}")
    names = ", ".join(sorted(str(getattr(n, "name", n.id)) for n in found))
    return Upstream(
        reason=(
            f"{wp_type} is not linked: the project has {len(found)} current {upstream} "
            f"records ({names}); pass source_node_ids to say which it came from"
        ),
        ambiguous=True,
    )


async def _linked_to(twin: Any, node_id: UUID, upstream: str) -> bool:
    from twin_core.models.enums import EdgeType

    for edge in await twin.get_edges(
        node_id, direction="outgoing", edge_type=EdgeType.GENERATED_FROM
    ):
        target = await twin.get_work_product(edge.target_id)
        if target is not None and _type_of(target) == upstream:
            return True
    return False


async def link_thread(twin: Any, project_id: str | UUID | None) -> ThreadLinks:
    """Link every current stage of the project to the stage before it, where one is clear.

    A stage that already has a ``GENERATED_FROM`` edge to a record of the
    stage before it is left alone. Otherwise the one current upstream record
    is linked; with several the stage is unlinked, with none it is missing.
    """
    from twin_core.models.enums import EdgeType

    out = ThreadLinks()
    if not project_id or not _can_walk(twin):
        return out
    pid = UUID(str(project_id))
    with tracer.start_as_current_span("twin.link_thread") as span:
        span.set_attribute("project_id", str(pid))
        try:
            for wp_type, upstream in UPSTREAM.items():
                for node in await current_records(twin, pid, wp_type):
                    if await _linked_to(twin, node.id, upstream):
                        continue
                    found = await infer_upstream(twin, pid, wp_type)
                    label = str((node.metadata or {}).get("item_key") or node.name)
                    if found.node_id is None:
                        bucket = out.unlinked if found.ambiguous else out.missing
                        bucket.append(f"{label}: {found.reason}")
                        continue
                    await twin.add_edge(node.id, UUID(found.node_id), EdgeType.GENERATED_FROM)
                    out.inferred.append(
                        {"node_id": str(node.id), "type": wp_type, "generated_from": found.node_id}
                    )
        except Exception as exc:  # noqa: BLE001 -- never fail the record over the sweep
            span.record_exception(exc)
            logger.warning("thread_link_failed", project_id=str(pid), error=str(exc))
            out.unlinked.append(f"the thread could not be checked: {exc}")
        span.set_attribute("thread.inferred", len(out.inferred))
        span.set_attribute("thread.unlinked", len(out.unlinked))
    logger.info(
        "thread_linked",
        project_id=str(pid),
        inferred=len(out.inferred),
        unlinked=len(out.unlinked),
        missing=len(out.missing),
    )
    return out
