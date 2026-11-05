"""The appearance record: how a product and its components look, and nothing else.

One ``appearance`` item per project (``APR-PROJECT``) holds the product's
look and each component's: colour (sRGB hex), finish, render material and
texture refs. Every change is the item's next revision, so the look has a
history like any other definition.

What an appearance change never does (3D-RC-007): rebuild CAD, touch the
manufacturing context, move a CAD, mesh or manufacturing head revision, or
mark anything stale. No interface edge starts or ends at an ``APR`` item, so
the impact walk never reaches anything from it.

**Across regeneration (SYNC-007).** A component's look is keyed by the
component's name (and remembers the node it was set on). The viewer applies
it to whatever mesh shows that component now, at any level: a new L0 concept
mesh, an L1 layout mesh or an L3 mesh tessellated from exact CAD all get the
stored look, because they carry the same component name. :func:`resolve` is
the same matching rule the dashboard's viewer uses.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, field_validator

from api_gateway.twin.change_classification import AppearanceValues
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.appearance")

ITEM_TYPE = "appearance"
ITEM_KEY = "APR-PROJECT"
_HEX = re.compile(r"^#[0-9a-f]{6}$")


class AppearanceError(ValueError):
    """A request that cannot be honoured. ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Appearance(BaseModel):
    """One look: colour, finish, render material and textures."""

    colour: str | None = None
    colour_name: str | None = Field(None, max_length=60)
    finish: str | None = Field(None, max_length=60)
    render_material: str | None = Field(None, max_length=80)
    texture_refs: list[str] = Field(default_factory=list, max_length=8)
    source: str = ""
    updated_at: str | None = None
    #: The person's words that set it, trimmed.
    request: str = Field("", max_length=300)

    @field_validator("colour")
    @classmethod
    def _hex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not _HEX.match(value):
            raise ValueError("colour must be an sRGB hex such as #c62828")
        return value


class ComponentAppearance(Appearance):
    """A component's look, keyed by its name so it outlives any one mesh."""

    key: str
    name: str | None = None
    node_id: str | None = None


class AppearanceRecord(BaseModel):
    """The product's look and each component's."""

    project_id: str
    product: Appearance | None = None
    components: list[ComponentAppearance] = Field(default_factory=list)


@dataclass
class StoredAppearance:
    record: AppearanceRecord
    revision: int
    node_id: str

    @property
    def ref(self) -> str:
        return f"{ITEM_KEY}@{self.revision}"


def component_key(name: str | None, node_id: str | None = None) -> str:
    """The stable key of a component: its name, normalised, else the node it was set on."""
    if name and name.strip():
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:80]
        if slug:
            return slug
    if node_id and node_id.strip():
        return f"node-{node_id.strip().lower()}"
    raise AppearanceError("a component needs a name or a node id")


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise AppearanceError(f"project id {project_id!r} is not a UUID") from exc


def _collector() -> Any:
    from observability.metrics import collector_for

    return collector_for("metaforge-gateway")


def _count(scope: str, outcome: str) -> None:
    try:
        _collector().record_appearance_update(scope, outcome)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a write
        logger.debug("appearance_metric_failed", error=str(exc))


async def read_appearance(twin: Any, project_id: str | UUID) -> StoredAppearance | None:
    """The current appearance record, or ``None`` when nothing was set."""
    from twin_core.items import find_item, supports_items

    if twin is None or not supports_items(twin):
        return None
    pid = _pid(project_id)
    item = await find_item(twin, ITEM_KEY, pid)
    if item is None or item.head_node_id is None:
        return None
    wp = await twin.get_work_product(item.head_node_id)
    raw = (getattr(wp, "metadata", None) or {}).get("appearance") if wp is not None else None
    if not isinstance(raw, dict):
        return None
    try:
        record = AppearanceRecord.model_validate(raw)
    except ValueError as exc:
        logger.warning("appearance_unreadable", node_id=str(item.head_node_id), error=str(exc))
        return None
    return StoredAppearance(
        record=record, revision=int(item.head_revision), node_id=str(item.head_node_id)
    )


def _merged(old: Appearance | None, values: AppearanceValues, **extra: Any) -> dict[str, Any]:
    """``old`` with the fields ``values`` names; a new colour replaces the old name."""
    out: dict[str, Any] = old.model_dump() if old is not None else {}
    if values.colour:
        out["colour"] = values.colour
        out["colour_name"] = values.colour_name
    for name in ("finish", "render_material"):
        value = getattr(values, name)
        if value:
            out[name] = value
    if values.texture_refs:
        out["texture_refs"] = list(values.texture_refs)[:8]
    out.update(extra)
    return out


_locks: dict[str, asyncio.Lock] = {}


async def set_appearance(
    twin: Any,
    project_id: str | UUID,
    values: AppearanceValues,
    *,
    component_name: str | None = None,
    component_id: str | None = None,
    source: str = "api",
    actor: str = "",
    request: str = "",
) -> StoredAppearance:
    """Record a look for one component, or for the product when none is named.

    Writes only the ``APR-PROJECT`` item: never CAD, a mesh, the manufacturing
    context or a stale mark.
    """
    from api_gateway.twin.item_revisions import finish_definition_revision
    from twin_core.items import plan_revision, supports_items
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    if values.is_empty():
        raise AppearanceError("name a colour, finish, render material or texture", 422)
    if twin is None or not supports_items(twin):
        raise AppearanceError("this twin cannot keep an appearance record", 503)
    pid = _pid(project_id)
    scope = "component" if (component_name or component_id) else "product"
    lock = _locks.setdefault(str(pid), asyncio.Lock())
    async with lock:
        with tracer.start_as_current_span("twin.appearance.set") as span:
            span.set_attribute("project_id", str(pid))
            span.set_attribute("appearance.scope", scope)
            try:
                stored = await read_appearance(twin, pid)
                record = (
                    stored.record.model_copy(deep=True)
                    if stored is not None
                    else AppearanceRecord(project_id=str(pid))
                )
                now = datetime.now(UTC).isoformat()
                extra = {"source": source, "updated_at": now, "request": request[:300]}
                if scope == "product":
                    record.product = Appearance(**_merged(record.product, values, **extra))
                    target = "product"
                else:
                    key = component_key(component_name, component_id)
                    old = next((c for c in record.components if c.key == key), None)
                    merged = _merged(old, values, **extra)
                    merged.update(
                        key=key,
                        name=component_name or (old.name if old else None),
                        node_id=component_id or (old.node_id if old else None),
                    )
                    record.components = [c for c in record.components if c.key != key] + [
                        ComponentAppearance(**merged)
                    ]
                    target = key
                plan = await plan_revision(
                    twin,
                    item_type=ITEM_TYPE,
                    name="Appearance",
                    project_id=pid,
                    author=actor or source,
                    item_key=ITEM_KEY,
                    change_reason=f"appearance of {target}",
                    run_id=None,
                )
                if plan is None:
                    raise AppearanceError("this twin cannot keep an appearance record", 503)
                record_json = record.model_dump(mode="json")
                content = json.dumps(record_json, sort_keys=True, separators=(",", ":")).encode()
                content_hash = hashlib.sha256(content).hexdigest()
                metadata: dict[str, Any] = {
                    "appearance": record_json,
                    "appearance_target": target,
                    "source": source,
                    #: Says what this revision is not, for anyone reading the graph.
                    "affects_engineering_model": False,
                    "content_sha256": content_hash,
                    **plan.stamp(),
                }
                stamp = datetime.now(UTC)
                wp_id = uuid4()
                created = await twin.create_work_product(
                    WorkProduct(
                        id=wp_id,
                        name="Appearance",
                        type=WorkProductType.APPEARANCE,
                        domain="industrial_design",
                        file_path="",
                        content_hash=content_hash,
                        format="json",
                        metadata=metadata,
                        created_at=stamp,
                        updated_at=stamp,
                        created_by=source,
                        project_id=pid,
                    )
                )
                node_id = getattr(created, "id", wp_id)
                result: dict[str, Any] = {}
                await finish_definition_revision(
                    twin, plan, node_id, name="Appearance", result=result
                )
            except AppearanceError:
                _count(scope, "refused")
                raise
            except Exception as exc:
                span.record_exception(exc)
                _count(scope, "failed")
                logger.warning("appearance_update_failed", project_id=str(pid), error=str(exc))
                raise
            out = StoredAppearance(
                record=record,
                revision=int(result.get("revision") or plan.revision),
                node_id=str(node_id),
            )
            span.set_attribute("appearance.ref", out.ref)
            _count(scope, "updated")
            logger.info(
                "appearance_updated",
                project_id=str(pid),
                ref=out.ref,
                target=target,
                source=source,
                colour=values.colour,
                finish=values.finish,
                render_material=values.render_material,
            )
            return out


def resolve(
    record: AppearanceRecord | None,
    *,
    name: str | None = None,
    node_id: str | None = None,
    mesh_name: str | None = None,
) -> Appearance | None:
    """The look a mesh gets: its node's, else its component name's, else the product's.

    The same rule the viewer applies, so a regenerated mesh (a new node, the
    same component name) keeps the stored look.
    """
    if record is None:
        return None
    if node_id:
        for comp in record.components:
            if comp.node_id and comp.node_id == node_id:
                return comp
    keys = set()
    for candidate in (name, mesh_name):
        if candidate and candidate.strip():
            try:
                keys.add(component_key(candidate))
            except AppearanceError:
                continue
    for comp in record.components:
        if comp.key in keys:
            return comp
    return record.product
