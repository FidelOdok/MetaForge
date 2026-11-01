"""Item read API (FORGE-523).

``GET /v1/twin/items?project_id=`` lists items (one row per versioned
definition, showing its head) and ``GET /v1/twin/items/{key}/revisions``
returns one item's history, oldest first. Read-only: items and revisions are
created by the existing write paths, never through this router.

FORGE-525: both show approved state only. An item whose revisions are all
still drafts of a run is not listed, and another run's open drafts are not in
a history. ``?run_id=`` on the history route reads it as that run does, with
the run's own drafts (opt-in, for reviewing what a gate would commit).

FORGE-526: each revision also lists the baselines that pin it (and the gate of
the first one, when the revision's own edge names no gate), and
``GET /v1/twin/items/{key}/diff?a=&b=`` compares two revisions
(``api_gateway.twin.item_diff``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.items")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


class ItemResponse(BaseModel):
    key: str
    item_type: str
    name: str
    project_id: str | None = None
    head_revision: int
    #: ``None`` only for an item that has drafts and no approved revision yet.
    head_node_id: str | None = None
    head_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    #: FORGE-525: set only when reading as a run that has a draft of this item.
    draft_revision: int | None = None
    draft_node_id: str | None = None
    draft_ref: str | None = None


class ItemListResponse(BaseModel):
    items: list[ItemResponse]
    total: int


class ItemRevisionResponse(BaseModel):
    revision: int
    node_id: str
    name: str | None = None
    change_reason: str | None = None
    run_id: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    is_head: bool
    adopted: bool
    #: FORGE-525: committed | draft | approved | rejected | abandoned.
    status: str = "committed"
    change_set: str | None = None
    phase: str | None = None
    gate: str | None = None
    status_reason: str | None = None
    #: FORGE-526: baselines (ids) that pin this revision, oldest first.
    baselines: list[str] = Field(default_factory=list)


class ItemCurrentResponse(BaseModel):
    revision: int
    node_id: str
    ref: str


class ItemLessonResponse(BaseModel):
    """A rejected revision, kept as "already tried, failed because" (FORGE-530)."""

    ref: str
    node_id: str
    run_id: str | None = None
    reason: str | None = None
    lesson: str


class ItemHistoryResponse(BaseModel):
    item: ItemResponse
    revisions: list[ItemRevisionResponse]
    #: What the reader sees as current: its run's draft, else the head.
    current: ItemCurrentResponse | None = None
    lessons: list[ItemLessonResponse] = []


class ItemDiffResponse(BaseModel):
    key: str
    item_type: str
    name: str
    a: dict[str, Any]
    b: dict[str, Any]
    a_ref: str
    b_ref: str
    geometry: dict[str, Any] | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    fields: list[dict[str, Any]] = Field(default_factory=list)
    dependents: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _twin() -> object:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _parse_project(project_id: str | None) -> UUID | None:
    if not project_id:
        return None
    try:
        return UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project_id format") from None


@router.get("/items", response_model=ItemListResponse)
async def list_twin_items(
    project_id: str | None = None,
    item_type: str | None = Query(default=None, description="e.g. cad_model, constraint_set"),
) -> ItemListResponse:
    """Versioned definitions, each at its current head."""
    from api_gateway.twin.item_revisions import item_to_dict
    from twin_core.items import list_items

    with tracer.start_as_current_span("twin.list_items") as span:
        scoped = _parse_project(project_id)
        if project_id:
            span.set_attribute("twin.filter.project_id", project_id)
        items = await list_items(_twin(), project_id=scoped, item_type=item_type)
        rows = [ItemResponse(**item_to_dict(i)) for i in items]
        span.set_attribute("twin.items_count", len(rows))
        logger.info("twin_items_listed", count=len(rows), project_id=project_id)
        return ItemListResponse(items=rows, total=len(rows))


@router.get("/items/{key}/revisions", response_model=ItemHistoryResponse)
async def get_item_revisions(
    key: str,
    project_id: str | None = None,
    run_id: str | None = Query(
        default=None, description="Read as this design-flow run: include its open drafts"
    ),
) -> ItemHistoryResponse:
    """Every revision of one item, oldest first. ``key`` may carry an ``@n`` suffix."""
    from api_gateway.twin.item_revisions import make_item_history_reader
    from twin_core.items import AmbiguousItemKeyError, ItemError, UnknownItemError

    with tracer.start_as_current_span("twin.item_revisions") as span:
        span.set_attribute("twin.item_key", key)
        _parse_project(project_id)
        read = make_item_history_reader(_twin())
        try:
            data = await read(item_key=key, project_id=project_id, run_id=run_id)
        except UnknownItemError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AmbiguousItemKeyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ItemError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ItemHistoryResponse(
            item=ItemResponse(**data["item"]),
            revisions=await _with_baselines(data),
            current=ItemCurrentResponse(**data["current"]) if data.get("current") else None,
            lessons=[ItemLessonResponse(**lesson) for lesson in data.get("lessons", [])],
        )


async def _with_baselines(data: dict[str, Any]) -> list[ItemRevisionResponse]:
    """Add the pinning baselines (and their gate, as a fallback) to each revision."""
    item = data["item"]
    pinned: dict[int, list[Any]] = {}
    if item.get("project_id"):
        twin: Any = _twin()
        baselines = sorted(
            await twin.list_baselines(project_id=UUID(item["project_id"])),
            key=lambda b: b.created_at,
        )
        for baseline in baselines:
            for pin in baseline.items:
                if pin.key == item["key"]:
                    pinned.setdefault(pin.revision, []).append(baseline)
    rows: list[ItemRevisionResponse] = []
    for raw in data["revisions"]:
        holders = pinned.get(raw["revision"], [])
        merged = {**raw, "baselines": [str(b.id) for b in holders]}
        if not merged.get("gate"):
            merged["gate"] = next((b.gate_id for b in holders if b.gate_id), None)
        rows.append(ItemRevisionResponse(**merged))
    return rows


@router.get("/items/{key}/diff", response_model=ItemDiffResponse)
async def get_item_diff(
    key: str,
    a: str | None = Query(default=None, description="Older revision, e.g. 2 or @2"),
    b: str | None = Query(default=None, description="Newer revision; defaults to the current one"),
    project_id: str | None = None,
) -> ItemDiffResponse:
    """Geometry delta, parameter, requirement and field changes between two revisions."""
    from api_gateway.twin.item_diff import make_item_differ
    from api_gateway.twin.routes import get_geometry_diff
    from twin_core.items import AmbiguousItemKeyError, ItemError, UnknownItemError

    with tracer.start_as_current_span("twin.item_diff_route") as span:
        span.set_attribute("twin.item_key", key)
        _parse_project(project_id)
        diff = make_item_differ(_twin(), geometry_diff_provider=get_geometry_diff)
        try:
            data = await diff(key, a=a, b=b, project_id=project_id)
        except UnknownItemError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AmbiguousItemKeyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ItemError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ItemDiffResponse(**data)
