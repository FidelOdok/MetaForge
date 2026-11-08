"""How a concept component is resolved once engineering has moved past it.

A concept assembly names components (``CMP-<slug>``) before anything is
dimensioned. Engineering then builds exact CAD for the product, and the parts
it builds do not always line up one to one with the concept's components: a
single illustrative "desk lamp enclosure" may be realised by a base, two arms,
a head and a diffuser, or dropped because a decision replaced it. Until now
nothing recorded either outcome, so the concept component stayed
``Illustrative`` and the release checklist's "every required component has a
validated exact model" could never pass.

A resolution is a design decision about one concept component, recorded as a
``design_decision`` work product (so it sits in the decision log, with its
reason and who took it):

``realised_by``
    The component is realised by one or more CAD items (``item_keys``). Its
    maturity is the least mature of those items, and those items become the
    required components the release checklist checks in its place.
``retired``
    The component is no longer part of the product (``reason`` says why). It
    stops being required; nothing is deleted.
``restored``
    Undo: the component is required again, as the concept named it.

The newest resolution of a component wins, and every earlier one stays in the
log, so a resolution is reversible and its history is reviewable. Nothing here
changes a CAD revision or the concept assembly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.component_resolution")

#: Metadata key on the decision that records a resolution.
RESOLUTION_KEY = "component_resolution"
ACTIONS: tuple[str, ...] = ("realised_by", "retired", "restored")
Action = Literal["realised_by", "retired", "restored"]
#: The item types that can realise a component.
REALISING_ITEM_TYPES = frozenset({"cad_model", "assembly"})
MAX_ITEMS = 50
MAX_REASON = 1000


class ComponentResolutionError(ValueError):
    """A resolution that cannot be recorded; ``status`` is its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _pid(project_id: UUID | str) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ComponentResolutionError(f"project id {project_id!r} is not a UUID") from exc


def _created(node: Any) -> str:
    stamp = getattr(node, "created_at", None)
    return stamp.isoformat() if stamp is not None else ""


async def resolution_records(twin: Any, project_id: UUID | str) -> list[Any]:
    """Every resolution decision of the project, oldest first."""
    from twin_core.models.enums import WorkProductType

    pid = _pid(project_id)
    try:
        decisions = await twin.list_work_products(
            work_product_type=WorkProductType.DESIGN_DECISION, project_id=pid
        )
    except Exception as exc:  # noqa: BLE001 - no list API reads as no resolutions
        logger.warning("component_resolutions_unavailable", error=str(exc))
        return []
    out = [
        d
        for d in decisions
        if isinstance((getattr(d, "metadata", None) or {}).get(RESOLUTION_KEY), dict)
    ]
    return sorted(out, key=_created)


async def current_resolutions(twin: Any, project_id: UUID | str) -> dict[str, dict[str, Any]]:
    """``{component id: resolution}`` for every component whose newest resolution stands.

    A component whose newest resolution is ``restored`` has none.
    """
    latest: dict[str, dict[str, Any]] = {}
    for node in await resolution_records(twin, project_id):
        body = dict((node.metadata or {})[RESOLUTION_KEY])
        cid = str(body.get("component_id") or "")
        if not cid:
            continue
        body["decision_node_id"] = str(node.id)
        latest[cid] = body
    return {cid: body for cid, body in latest.items() if body.get("action") != "restored"}


async def _realising_items(
    twin: Any, pid: UUID, item_keys: list[str]
) -> tuple[list[str], list[str]]:
    """``(keys, problems)``: each key normalised, and why any cannot realise a component."""
    from twin_core.items import find_item, parse_item_ref

    keys: list[str] = []
    problems: list[str] = []
    for raw in item_keys:
        try:
            key, _pin = parse_item_ref(str(raw))
        except ValueError as exc:
            problems.append(str(exc))
            continue
        item = await find_item(twin, key, pid)
        if item is None:
            problems.append(f"{key} is not an item of this project")
        elif str(getattr(item, "item_type", "") or "") not in REALISING_ITEM_TYPES:
            problems.append(f"{key} is a {item.item_type}, not a CAD part or assembly")
        elif key not in keys:
            keys.append(key)
    return keys, problems


async def record_resolution(
    twin: Any,
    project_id: UUID | str,
    *,
    component_id: str,
    action: str,
    reason: str,
    item_keys: list[str] | None = None,
    actor: str = "",
    on_behalf_of: str = "",
) -> dict[str, Any]:
    """Record one resolution of a concept component and return it.

    Refused (``ComponentResolutionError``) for a component the project's
    concept assemblies never named, an unknown action, a missing reason, or a
    ``realised_by`` whose items are not CAD items of this project.
    """
    from twin_core.component_identity import is_component_id, project_identity
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.component_resolution.record") as span:
        span.set_attribute("component.id", component_id)
        span.set_attribute("resolution.action", action)
        if action not in ACTIONS:
            raise ComponentResolutionError(f"action is one of {', '.join(ACTIONS)}, not {action!r}")
        reason = (reason or "").strip()
        if not reason:
            raise ComponentResolutionError("a resolution needs a reason")
        if not is_component_id(component_id):
            raise ComponentResolutionError(
                f"{component_id!r} is not a concept component id (CMP-...)"
            )
        ident = await project_identity(twin, pid)
        comp = (ident.get("components") or {}).get(component_id)
        if comp is None:
            known = ", ".join(sorted(ident.get("components") or {})) or "none"
            raise ComponentResolutionError(
                f"{component_id} is not a component of this project's concept (known: {known})",
                404,
            )
        keys: list[str] = []
        if action == "realised_by":
            if not item_keys:
                raise ComponentResolutionError("realised_by needs the item_keys that realise it")
            if len(item_keys) > MAX_ITEMS:
                raise ComponentResolutionError(f"at most {MAX_ITEMS} items realise one component")
            keys, problems = await _realising_items(twin, pid, list(item_keys))
            if problems:
                raise ComponentResolutionError("; ".join(problems))
        elif item_keys:
            raise ComponentResolutionError(f"{action} takes no item_keys")
        previous = (await current_resolutions(twin, pid)).get(component_id)
        if action == "restored" and previous is None:
            raise ComponentResolutionError(f"{component_id} has no resolution to undo", 409)

        now = datetime.now(UTC)
        body: dict[str, Any] = {
            "component_id": component_id,
            "component_name": str(comp.get("name") or component_id),
            "action": action,
            "item_keys": keys,
            "reason": reason[:MAX_REASON],
            "by": actor or "agent",
            "on_behalf_of": on_behalf_of or None,
            "at": now.isoformat(),
            "replaces": previous.get("decision_node_id") if previous else None,
        }
        name = str(comp.get("name") or component_id)
        title = {
            "realised_by": f"{name} is realised by {', '.join(keys)}",
            "retired": f"{name} is retired from the product",
            "restored": f"{name} is required again",
        }[action]
        content = json.dumps(body, sort_keys=True).encode("utf-8")
        metadata: dict[str, Any] = {
            RESOLUTION_KEY: body,
            "rationale": body["reason"],
            "alternatives": [],
            "recorded_by": "twin.resolve_concept_component",
            "component_id": component_id,
        }
        if previous:
            metadata["supersedes"] = previous.get("decision_node_id")
        wp_id = uuid4()
        created = await twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=title[:200],
                type=WorkProductType.DESIGN_DECISION,
                domain="mechanical",
                file_path="",
                content_hash=hashlib.sha256(content).hexdigest(),
                format="json",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by="twin.resolve_concept_component",
                project_id=pid,
            )
        )
        node_id = str(getattr(created, "id", wp_id))
        logger.info(
            "concept_component_resolved",
            project_id=str(pid),
            component_id=component_id,
            action=action,
            items=keys,
            decision_node_id=node_id,
        )
        return {**body, "decision_node_id": node_id, "title": title}


__all__ = [
    "ACTIONS",
    "REALISING_ITEM_TYPES",
    "RESOLUTION_KEY",
    "ComponentResolutionError",
    "current_resolutions",
    "record_resolution",
    "resolution_records",
]
