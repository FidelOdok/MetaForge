"""Release package API (FORGE-299, gap G-I3).

``POST /v1/releases`` and ``GET /v1/releases`` wire the dashboard's release
view to the real ``twin.create_release_package``/list evaluators
(``api_gateway.twin.release_package``) -- same thin-REST-wrapper-over-an-
injected-closure pattern ``api_gateway/dfm/routes.py`` established for
``twin.evaluate_overhang_metric``: the dashboard has no MCP client of its
own, so this is the second REST-reachable route in the
``twin.evaluate_*``/``twin.create_*`` family.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.releases")

router = APIRouter(prefix="/v1/releases", tags=["releases"])

_create_release: Any = None
_list_releases: Any = None


def init_release_package(creator: Any, lister: Any) -> None:
    """Bind the real ``create(...)``/``list_packages(...)`` closures (built
    in ``api_gateway/server.py`` from ``make_release_package_creator``/
    ``make_release_package_lister``)."""
    global _create_release, _list_releases  # noqa: PLW0603
    _create_release = creator
    _list_releases = lister
    logger.info("release_package_routes_initialized")


class CreateReleaseRequest(BaseModel):
    project_id: str
    notes: str | None = None


class ReleaseSnapshot(BaseModel):
    hierarchy_node_ids: list[str]
    bom_item_ids: list[str]
    evidence_ids: list[str]
    decision_ids: list[str]
    drawing_ids: list[str]


class ReleaseDiff(BaseModel):
    compared_to: str | None
    hierarchy_delta: int
    bom_delta: int
    evidence_delta: int
    decision_delta: int


class ReleasePackageResponse(BaseModel):
    node_id: str
    created_at: str | None = None
    title: str | None = None
    statement: str | None = None
    snapshot: ReleaseSnapshot
    diff_from_previous: ReleaseDiff
    gate_status: str


class ReleasePackageListResponse(BaseModel):
    releases: list[ReleasePackageResponse]


@router.post("", response_model=ReleasePackageResponse)
async def create_release(body: CreateReleaseRequest) -> ReleasePackageResponse:
    if _create_release is None:
        raise HTTPException(status_code=503, detail="release package creator not configured")
    with tracer.start_as_current_span("releases.create") as span:
        span.set_attribute("release.project_id", body.project_id)
        try:
            result = await _create_release(project_id=body.project_id, notes=body.notes)
        except ValueError as exc:
            # G8-not-passed and other validation failures are a 409 (the
            # request is well-formed but the project isn't release-ready
            # yet), not a 400.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("release_package_create_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"release package creation failed: {exc}"
            ) from exc
        return ReleasePackageResponse(
            node_id=result["node_id"],
            created_at=result["created_at"],
            title=result["title"],
            statement=result["statement"],
            snapshot=ReleaseSnapshot(**result["snapshot"]),
            diff_from_previous=ReleaseDiff(**result["diff_from_previous"]),
            gate_status=result["gate_status"],
        )


@router.get("", response_model=ReleasePackageListResponse)
async def list_releases(project_id: str) -> ReleasePackageListResponse:
    if _list_releases is None:
        raise HTTPException(status_code=503, detail="release package lister not configured")
    with tracer.start_as_current_span("releases.list") as span:
        span.set_attribute("release.project_id", project_id)
        packages = await _list_releases(project_id=project_id)
        return ReleasePackageListResponse(
            releases=[
                ReleasePackageResponse(
                    node_id=p["node_id"],
                    created_at=p["created_at"],
                    title=p["title"],
                    statement=p["statement"],
                    snapshot=ReleaseSnapshot(**p["snapshot"]),
                    diff_from_previous=ReleaseDiff(**p["diff_from_previous"]),
                    gate_status=p["gate_status"],
                )
                for p in packages
            ]
        )
