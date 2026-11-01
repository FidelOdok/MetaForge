"""Baselines, the current view and run changes (FORGE-526).

Read-only routes over items (FORGE-523) and baselines (FORGE-51, extended
with item pins):

- ``GET /v1/twin/baselines?project_id=`` lists a project's baselines, newest
  first.
- ``GET /v1/twin/baselines/diff?a=&b=`` compares two baselines item by item
  (``unchanged``, ``changed @x -> @y``, ``added``, ``removed``). ``b=current``
  compares ``a`` with the project's current items.
- ``GET /v1/twin/baselines/{id}`` returns one baseline with its pins.
- ``GET /v1/twin/current-view?project_id=`` is the project page's default
  view: one row per item at its current revision, records with an
  out-of-date flag, counts and readiness over current items only.
- ``GET /v1/twin/runs/{run_id}/changes`` lists the revisions a run produced
  and the baselines its gates recorded.
- ``GET /v1/twin/revision-index?project_id=`` maps node ids (revisions, and a
  constraint set's constraints) to ``KEY@n``, for revision badges on the BOM
  and Requirements pages.

Baselines are created by gate approvals (``create_item_baseline``) and by
``twin.create_baseline``, never through this router.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.baselines")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BaselineItemRefResponse(BaseModel):
    key: str
    item_type: str
    revision: int
    ref: str
    node_id: str
    name: str = ""


class BaselineMemberResponse(BaseModel):
    entity_kind: str
    entity_id: str
    revision: int


class BaselineSummary(BaseModel):
    id: str
    name: str
    project_id: str | None = None
    created_at: str
    approved_by: list[str]
    reason: str = ""
    gate_id: str | None = None
    run_id: str | None = None
    source: str = "manual"
    item_count: int
    member_count: int


class BaselineDetail(BaselineSummary):
    items: list[BaselineItemRefResponse] = Field(default_factory=list)
    members: list[BaselineMemberResponse] = Field(default_factory=list)


class BaselineListResponse(BaseModel):
    baselines: list[BaselineSummary]
    total: int


class BaselineItemDiffResponse(BaseModel):
    key: str
    item_type: str
    name: str
    status: str
    from_revision: int | None = None
    to_revision: int | None = None
    from_ref: str | None = None
    to_ref: str | None = None


class BaselineDiffResponse(BaseModel):
    a: BaselineSummary
    b: BaselineSummary | None = None
    b_is_current: bool = False
    items: list[BaselineItemDiffResponse]
    counts: dict[str, int]


class RevisionStateResponse(BaseModel):
    revision: int
    node_id: str
    status: str
    name: str | None = None
    run_id: str | None = None
    gate_id: str | None = None
    author: str | None = None
    change_reason: str | None = None
    created_at: str | None = None
    adopted: bool = False


class CurrentItemRow(BaseModel):
    key: str
    item_type: str
    name: str
    revision: int | None = None
    ref: str | None = None
    node_id: str | None = None
    revision_status: str
    validation_status: str
    run_id: str | None = None
    gate_id: str | None = None
    change_reason: str | None = None
    author: str | None = None
    updated_at: str | None = None
    revision_count: int
    evidence_state: str
    evidence_count: int
    drafts: list[RevisionStateResponse] = Field(default_factory=list)


class AnalysedRef(BaseModel):
    key: str
    revision: int
    ref: str
    current: bool


class RecordRowResponse(BaseModel):
    node_id: str
    record_type: str
    name: str
    created_at: str | None = None
    analysed: list[AnalysedRef] = Field(default_factory=list)
    out_of_date: bool = False
    #: FORGE-527's recorded status (current, stale, invalid, superseded, revalidated).
    staleness: str | None = None


class OtherRow(BaseModel):
    node_id: str
    name: str
    type: str
    validation_status: str
    updated_at: str | None = None


class ItemGroup(BaseModel):
    item_type: str
    count: int
    keys: list[str]


class CurrentViewResponse(BaseModel):
    project_id: str | None = None
    items: list[CurrentItemRow]
    groups: list[ItemGroup]
    records: list[RecordRowResponse]
    other: list[OtherRow]
    counts: dict[str, Any]
    readiness: int
    latest_baseline: BaselineSummary | None = None


class RevisionIndexEntry(BaseModel):
    key: str
    item_type: str
    revision: int
    ref: str
    status: str
    current: bool
    revision_count: int
    via: str


class RevisionIndexResponse(BaseModel):
    project_id: str
    nodes: dict[str, RevisionIndexEntry]


class RunRevisionRow(BaseModel):
    key: str
    item_type: str
    name: str
    revision: int
    ref: str
    node_id: str
    status: str
    change_reason: str | None = None
    created_at: str | None = None
    is_current: bool
    baselined_by_gate: str | None = None


class RunChangesResponse(BaseModel):
    run_id: str
    revisions: list[RunRevisionRow]
    baselines: list[BaselineSummary]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {label} format") from None


def _summary(baseline: Any) -> BaselineSummary:
    from api_gateway.twin.baseline import baseline_to_dict

    return BaselineSummary(**baseline_to_dict(baseline, include_items=False))


async def _get_baseline(baseline_id: str) -> Any:
    baseline = await _twin().get_baseline(_uuid(baseline_id, "baseline id"))
    if baseline is None:
        raise HTTPException(status_code=404, detail=f"baseline {baseline_id} not found")
    return baseline


async def _project_work_products(project_id: str) -> list[dict[str, Any]]:
    """The project's linked work products (status source); empty when unknown."""
    try:
        from api_gateway.projects.routes import get_project_backend

        project = await get_project_backend().get_project(project_id)
    except Exception as exc:  # noqa: BLE001 -- the view still works from the twin
        logger.warning("current_view_project_unavailable", project_id=project_id, error=str(exc))
        return []
    if project is None:
        return []
    return [wp.model_dump() for wp in project.work_products]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/baselines", response_model=BaselineListResponse)
async def list_baselines(project_id: str | None = None) -> BaselineListResponse:
    """A project's baselines, newest first."""
    with tracer.start_as_current_span("twin.list_baselines") as span:
        pid = _uuid(project_id, "project_id") if project_id else None
        baselines = await _twin().list_baselines(project_id=pid)
        baselines = sorted(baselines, key=lambda b: b.created_at, reverse=True)
        span.set_attribute("twin.baselines_count", len(baselines))
        logger.info("twin_baselines_listed", project_id=project_id, count=len(baselines))
        rows = [_summary(b) for b in baselines]
        return BaselineListResponse(baselines=rows, total=len(rows))


@router.get("/baselines/diff", response_model=BaselineDiffResponse)
async def diff_baselines_route(
    a: str = Query(..., description="Baseline id"),
    b: str = Query(..., description="Baseline id, or 'current' for the project's current items"),
) -> BaselineDiffResponse:
    """Per item: unchanged, changed (@x -> @y), added or removed between ``a`` and ``b``."""
    from twin_core.transactions.baseline import baseline_item_refs, diff_baselines

    with tracer.start_as_current_span("twin.diff_baselines") as span:
        span.set_attribute("twin.baseline.a", a)
        span.set_attribute("twin.baseline.b", b)
        left = await _get_baseline(a)
        right = None
        if b == "current":
            if left.project_id is None:
                raise HTTPException(
                    status_code=400, detail="baseline a has no project, so it has no current view"
                )
            right_items = await baseline_item_refs(_twin(), left.project_id)
        else:
            right = await _get_baseline(b)
            right_items = list(right.items)
        diffs = diff_baselines(list(left.items), right_items)
        counts = {s: 0 for s in ("unchanged", "changed", "added", "removed")}
        for d in diffs:
            counts[d.status] += 1
        logger.info("twin_baselines_diffed", a=a, b=b, **counts)
        return BaselineDiffResponse(
            a=_summary(left),
            b=_summary(right) if right is not None else None,
            b_is_current=right is None,
            items=[BaselineItemDiffResponse(**d.to_dict()) for d in diffs],
            counts=counts,
        )


@router.get("/baselines/{baseline_id}", response_model=BaselineDetail)
async def get_baseline(baseline_id: str) -> BaselineDetail:
    """One baseline with its item pins and constraint/entity members."""
    from api_gateway.twin.baseline import baseline_to_dict

    with tracer.start_as_current_span("twin.get_baseline") as span:
        span.set_attribute("twin.baseline.id", baseline_id)
        baseline = await _get_baseline(baseline_id)
        return BaselineDetail(**baseline_to_dict(baseline))


@router.get("/current-view", response_model=CurrentViewResponse)
async def get_current_view(project_id: str = Query(...)) -> CurrentViewResponse:
    """The project's current items, records, counts and readiness (current items only)."""
    from twin_core.items.current import build_current_view

    with tracer.start_as_current_span("twin.current_view") as span:
        pid = _uuid(project_id, "project_id")
        span.set_attribute("project.id", project_id)
        twin = _twin()
        baselines = await twin.list_baselines(project_id=pid)
        view = await build_current_view(
            twin,
            pid,
            project_work_products=await _project_work_products(project_id),
            baselines=baselines,
        )
        latest = max(baselines, key=lambda b: b.created_at) if baselines else None
        return CurrentViewResponse(
            project_id=project_id,
            items=[CurrentItemRow(**r) for r in view.items],
            groups=[ItemGroup(**g) for g in view.groups],
            records=[RecordRowResponse(**r) for r in view.records],
            other=[OtherRow(**o) for o in view.other],
            counts=view.counts,
            readiness=view.readiness,
            latest_baseline=_summary(latest) if latest is not None else None,
        )


@router.get("/runs/{run_id}/changes", response_model=RunChangesResponse)
async def get_run_changes(run_id: str, project_id: str | None = None) -> RunChangesResponse:
    """Revisions a design-flow run produced, and the baselines its gates recorded."""
    from twin_core.items import list_items
    from twin_core.items.current import current_revision, revision_states

    with tracer.start_as_current_span("twin.run_changes") as span:
        span.set_attribute("run.id", run_id)
        twin = _twin()
        pid = _uuid(project_id, "project_id") if project_id else None
        baselines = [b for b in await twin.list_baselines(project_id=pid) if b.run_id == run_id]
        baselines.sort(key=lambda b: b.created_at)
        gate_for_ref: dict[str, str] = {}
        for baseline in baselines:
            for pin in baseline.items:
                gate_for_ref.setdefault(pin.ref, baseline.gate_id or baseline.name)
        rows: list[RunRevisionRow] = []
        for item in await list_items(twin, project_id=pid, include_unheaded=True):
            states = await revision_states(twin, item)
            cur = current_revision(states, item.head_node_id)
            for state in states:
                if state.run_id != run_id:
                    continue
                ref = f"{item.key}@{state.revision}"
                rows.append(
                    RunRevisionRow(
                        key=item.key,
                        item_type=item.item_type,
                        name=state.name or item.name,
                        revision=state.revision,
                        ref=ref,
                        node_id=str(state.node_id),
                        status=state.status,
                        change_reason=state.change_reason,
                        created_at=state.created_at.isoformat() if state.created_at else None,
                        is_current=cur is not None and cur.revision == state.revision,
                        baselined_by_gate=state.gate_id or gate_for_ref.get(ref),
                    )
                )
        rows.sort(key=lambda r: (r.created_at or "", r.key))
        span.set_attribute("run.revision_count", len(rows))
        logger.info(
            "twin_run_changes", run_id=run_id, revisions=len(rows), baselines=len(baselines)
        )
        return RunChangesResponse(
            run_id=run_id, revisions=rows, baselines=[_summary(b) for b in baselines]
        )


@router.get("/revision-index", response_model=RevisionIndexResponse)
async def get_revision_index(project_id: str = Query(...)) -> RevisionIndexResponse:
    """Node id -> ``KEY@n`` for every revision (and constraint-set constraint) of the project."""
    from twin_core.items.current import revision_index

    with tracer.start_as_current_span("twin.revision_index") as span:
        pid = _uuid(project_id, "project_id")
        span.set_attribute("project.id", project_id)
        index = await revision_index(_twin(), pid)
        logger.info("twin_revision_index", project_id=project_id, nodes=len(index))
        return RevisionIndexResponse(
            project_id=project_id,
            nodes={k: RevisionIndexEntry(**v) for k, v in index.items()},
        )
