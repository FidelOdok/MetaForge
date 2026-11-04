"""Interface edges and cross-domain impact routes (FORGE-793).

Under ``/v1/twin/projects/{project_id}``:

- ``GET .../interfaces``: every interface edge (stored and derived), plus the
  items they join, for the dependency graph.
- ``POST .../interfaces``: a person declares an edge (confirmed at once).
- ``POST .../interfaces/propose``: propose edges from recorded data; each is
  ``proposed`` until confirmed.
- ``POST .../interfaces/{edge_id}/confirm``: a person confirms a proposal.
- ``DELETE .../interfaces/{edge_id}``: a person removes an edge (kept as
  ``removed``, never deleted).
- ``POST .../impact/preview``: the impact of a proposed change. Writes nothing.
- ``GET .../impact``: recorded impacts, newest first.
- ``GET .../impact/{change_or_revision}``: one recorded impact, by its id or
  by the revision it was for (``KEY@n``; the latest for a bare ``KEY``).

The logic lives in :mod:`twin_core.impact` and
:mod:`api_gateway.twin.impact_service`. This is product dependency, not the
workflow-step DAG of ``orchestrator/dependency_engine.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.impact_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InterfaceDeclareRequest(BaseModel):
    type: str = Field(description="mounts_to, clearance_to, connects_via, pin_maps_to, ...")
    source: str = Field(description="An item key (KEY or KEY@n) or record:<uuid>")
    target: str = Field(description="An item key (KEY or KEY@n) or record:<uuid>")
    coupled_parameters: list[str] = Field(default_factory=list)
    rationale: str = ""


class InterfaceRemoveRequest(BaseModel):
    reason: str = ""


class InterfaceProposeRequest(BaseModel):
    #: Limit the pass to pairs including this item.
    item_key: str | None = None


class GraphItem(BaseModel):
    ref: str
    label: str
    domain: str
    item_type: str = ""
    head_revision: int | None = None


class InterfaceListResponse(BaseModel):
    project_id: str
    edges: list[dict[str, Any]]
    nodes: list[GraphItem]


class InterfaceEdgeResponse(BaseModel):
    edge: dict[str, Any]
    created: bool = False


class InterfaceProposeResponse(BaseModel):
    proposed: list[dict[str, Any]]
    already: list[dict[str, Any]]
    skipped: list[str]


class ImpactPreviewRequest(BaseModel):
    item_key: str = Field(description="The item that would change (KEY)")
    old_revision: int | None = None
    new_revision: int | None = None
    changed_parameters: list[str] | None = None
    max_depth: int = Field(default=4, ge=1, le=8)
    include_unaffected: bool = False


class ImpactListResponse(BaseModel):
    project_id: str
    impacts: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    twin = get_twin()
    if twin is None:
        raise HTTPException(status_code=503, detail="The digital twin is not available")
    return twin


def _check(project_id: str) -> None:
    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None


def _actor(request: Request) -> str:
    from api_gateway.auth.approver import approver_from_request

    return approver_from_request(request).label


async def _call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run one call, mapping refusals to 400/404/409 and anything else to 502."""
    from twin_core.impact.interfaces import InterfaceError, UnknownInterfaceError
    from twin_core.items import ItemError, UnknownItemError

    _check(project_id)
    with tracer.start_as_current_span(f"twin.impact_routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await fn()
        except (UnknownInterfaceError, UnknownItemError) as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (InterfaceError, ItemError) as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("impact_route_failed", route=name, project_id=project_id, error=str(exc))
            raise HTTPException(
                status_code=502, detail="The interfaces or impact could not be read or changed"
            ) from exc


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/interfaces", response_model=InterfaceListResponse)
async def list_project_interfaces(
    project_id: str,
    include_removed: bool = Query(default=False),
    include_derived: bool = Query(default=True),
) -> InterfaceListResponse:
    """Every interface edge in the project, and the items they join.

    Derived edges (``simulation_input_of`` from a simulation's own pins,
    ``feature_of`` from the sketch layer) carry ``derived: true``. An edge
    whose pinned revision is behind the item's head says so in
    ``source_behind`` / ``target_behind``.
    """

    async def run() -> InterfaceListResponse:
        from twin_core.impact.interfaces import list_interfaces
        from twin_core.items import list_items
        from twin_core.models.interface import domain_for_item_type, is_record_ref

        twin = _twin()
        edges = await list_interfaces(
            twin, project_id, include_removed=include_removed, include_derived=include_derived
        )
        items = {i.key: i for i in await list_items(twin, project_id=project_id)}
        nodes: dict[str, GraphItem] = {}
        views: list[dict[str, Any]] = []
        for edge in edges:
            view = edge.to_view()
            for side, end in (("source", edge.source), ("target", edge.target)):
                item = items.get(end.ref)
                view[f"{side}_behind"] = bool(
                    item is not None
                    and end.revision is not None
                    and end.revision < item.head_revision
                )
                if end.ref not in nodes:
                    nodes[end.ref] = GraphItem(
                        ref=end.ref,
                        label=(item.name if item else end.label) or end.ref,
                        domain=domain_for_item_type(item.item_type) if item else end.domain,
                        item_type=item.item_type if item else end.item_type,
                        head_revision=item.head_revision
                        if item and not is_record_ref(end.ref)
                        else None,
                    )
            views.append(view)
        return InterfaceListResponse(project_id=project_id, edges=views, nodes=list(nodes.values()))

    return await _call("list_interfaces", project_id, run)


@router.post("/projects/{project_id}/interfaces", response_model=InterfaceEdgeResponse)
async def declare_project_interface(
    project_id: str, body: InterfaceDeclareRequest, request: Request
) -> InterfaceEdgeResponse:
    """A person declares an interface edge; it is confirmed at once.

    Declaring a link an inference already proposed confirms that proposal.
    """

    async def run() -> InterfaceEdgeResponse:
        from twin_core.impact.interfaces import declare_interface
        from twin_core.models.interface import InterfaceType

        try:
            kind = InterfaceType(body.type)
        except ValueError as exc:
            raise ValueError(
                f"unknown interface type {body.type!r}; one of "
                f"{', '.join(t.value for t in InterfaceType)}"
            ) from exc
        edge, created = await declare_interface(
            _twin(),
            project_id,
            type=kind,
            source_ref=body.source,
            target_ref=body.target,
            declared_by="human",
            actor=_actor(request),
            coupled_parameters=body.coupled_parameters,
            rationale=body.rationale,
        )
        return InterfaceEdgeResponse(edge=edge.to_view(), created=created)

    return await _call("declare_interface", project_id, run)


@router.post("/projects/{project_id}/interfaces/propose", response_model=InterfaceProposeResponse)
async def propose_project_interfaces(
    project_id: str, body: InterfaceProposeRequest
) -> InterfaceProposeResponse:
    """Propose edges from recorded data (board and enclosure fields, pin map and
    firmware sources). Each new one is ``proposed``, never confirmed."""

    async def run() -> InterfaceProposeResponse:
        from api_gateway.twin.interface_proposals import propose_interfaces

        outcome = await propose_interfaces(_twin(), project_id, item_key=body.item_key)
        return InterfaceProposeResponse(
            proposed=[e.to_view() for e in outcome.proposed],
            already=[e.to_view() for e in outcome.already],
            skipped=outcome.skipped,
        )

    return await _call("propose_interfaces", project_id, run)


@router.post(
    "/projects/{project_id}/interfaces/{edge_id}/confirm", response_model=InterfaceEdgeResponse
)
async def confirm_project_interface(
    project_id: str, edge_id: str, request: Request
) -> InterfaceEdgeResponse:
    """A person confirms an edge. It is re-pinned to both items' current heads."""

    async def run() -> InterfaceEdgeResponse:
        from twin_core.impact.interfaces import confirm_interface

        edge = await confirm_interface(_twin(), project_id, edge_id, actor=_actor(request))
        return InterfaceEdgeResponse(edge=edge.to_view())

    return await _call("confirm_interface", project_id, run)


@router.delete("/projects/{project_id}/interfaces/{edge_id}", response_model=InterfaceEdgeResponse)
async def remove_project_interface(
    project_id: str, edge_id: str, request: Request, reason: str = Query(default="")
) -> InterfaceEdgeResponse:
    """A person removes an edge. It is kept as ``removed`` and never re-proposed."""

    async def run() -> InterfaceEdgeResponse:
        from twin_core.impact.interfaces import remove_interface

        edge = await remove_interface(
            _twin(), project_id, edge_id, actor=_actor(request), reason=reason
        )
        return InterfaceEdgeResponse(edge=edge.to_view())

    return await _call("remove_interface", project_id, run)


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/impact/preview")
async def preview_project_impact(project_id: str, body: ImpactPreviewRequest) -> dict[str, Any]:
    """What a proposed change to ``item_key`` would affect. Writes nothing.

    The new revision defaults to the head plus one. ``changed_parameters``
    narrows the walk to edges whose coupled parameters it touches; leave it
    out when unknown. Reruns carry the disposition the project's approval
    policy would give them.
    """

    async def run() -> dict[str, Any]:
        from api_gateway.twin.impact_service import refine_dispositions
        from twin_core.impact.cross_domain import ChangeSpec, analyse_change, count_impact

        twin = _twin()
        result = await analyse_change(
            twin,
            project_id,
            ChangeSpec(
                item_key=body.item_key,
                old_revision=body.old_revision,
                new_revision=body.new_revision,
                changed_parameters=body.changed_parameters,
            ),
            preview=True,
            max_depth=body.max_depth,
            include_unaffected=body.include_unaffected,
        )
        result = await refine_dispositions(twin, result)
        count_impact("previewed", "api")
        logger.info(
            "cross_domain_impact_previewed",
            project_id=project_id,
            item_key=result.change.item_key,
            impacted=len(result.impacted),
            stale_results=len(result.stale_results),
        )
        return result.to_view()

    return await _call("preview_impact", project_id, run)


@router.get("/projects/{project_id}/impact", response_model=ImpactListResponse)
async def list_project_impacts(
    project_id: str, limit: int = Query(default=20, ge=1, le=200)
) -> ImpactListResponse:
    """Recorded impacts, newest first."""

    async def run() -> ImpactListResponse:
        from twin_core.impact.cross_domain import list_impacts

        found = await list_impacts(_twin(), project_id)
        return ImpactListResponse(
            project_id=project_id, impacts=[r.to_view() for r in found[:limit]]
        )

    return await _call("list_impacts", project_id, run)


@router.get("/projects/{project_id}/impact/{change_or_revision}")
async def get_project_impact(project_id: str, change_or_revision: str) -> dict[str, Any]:
    """One recorded impact: by its id, by ``KEY@n`` (the change to that
    revision), or by a bare ``KEY`` (its latest change)."""

    async def run() -> dict[str, Any]:
        from twin_core.impact.cross_domain import get_impact

        found = await get_impact(_twin(), project_id, change_or_revision)
        if found is None:
            raise HTTPException(
                status_code=404, detail=f"no recorded impact for {change_or_revision!r}"
            )
        return found.to_view()

    return await _call("get_impact", project_id, run)
