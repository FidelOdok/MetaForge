"""Interface contract routes (lamp golden path, step 1).

Under ``/v1/twin/projects/{project_id}``:

- ``GET .../contracts``: every contract with its coupled parameters, links,
  agreements for the current interface version, the latest recorded check
  (and whether it went stale) and the status: proposed, agreed, violated or
  stale. ``create_line`` is the plain sentence Create shows.
- ``POST .../contracts/check``: evaluate every contract (or one, by ``key``)
  against the current revisions and record each result.
- ``POST .../contracts/{key}/links``: name the item that implements one side.
- ``POST .../contracts/{key}/agree``: one owning domain agrees, as a person's
  recorded design decision.

The logic lives in :mod:`api_gateway.twin.interface_contracts`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.interface_contract_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

T = TypeVar("T")


class ContractCheckRequest(BaseModel):
    #: Check only this contract (``IFC-CAD-EDA``); every contract when omitted.
    key: str | None = None


class ContractLinkRequest(BaseModel):
    role: str = Field(description="The side: enclosure, pcb, pinmap, firmware, ...")
    item_key: str | None = Field(default=None, description="The item (KEY); null unlinks")


class ContractAgreeRequest(BaseModel):
    domain: str = Field(description="The owning domain agreeing (mechanical, electronics, ...)")
    rationale: str = Field(default="", max_length=2000)


class ContractListResponse(BaseModel):
    project_id: str
    contracts: list[dict[str, Any]]


class ContractCheckResponse(BaseModel):
    project_id: str
    checks: list[dict[str, Any]]


class ContractAgreeResponse(BaseModel):
    decision_id: str | None = None
    created: bool
    contract: str


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    twin = get_twin()
    if twin is None:
        raise HTTPException(status_code=503, detail="The digital twin is not available")
    return twin


def _actor(request: Request) -> str:
    from api_gateway.auth.approver import approver_from_request

    return approver_from_request(request).label


def _backend() -> Any:
    from api_gateway.projects.routes import get_project_backend

    return get_project_backend()


async def _call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    from api_gateway.twin.interface_contracts import ContractError
    from twin_core.items import ItemError

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None
    with tracer.start_as_current_span(f"twin.contract_routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await fn()
        except ContractError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ItemError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning(
                "contract_route_failed", route=name, project_id=project_id, error=str(exc)
            )
            raise HTTPException(
                status_code=502, detail="The interface contracts could not be read or changed"
            ) from exc


@router.get("/projects/{project_id}/contracts", response_model=ContractListResponse)
async def list_project_contracts(project_id: str) -> ContractListResponse:
    """Every interface contract, its latest check and its status."""
    from api_gateway.twin.interface_contracts import contracts_view

    twin = _twin()
    view = await _call("list", project_id, lambda: contracts_view(twin, project_id))
    return ContractListResponse(**view)


@router.post("/projects/{project_id}/contracts/check", response_model=ContractCheckResponse)
async def check_project_contracts(
    project_id: str, body: ContractCheckRequest, request: Request
) -> ContractCheckResponse:
    """Evaluate the contracts against the current revisions and record each result."""
    from api_gateway.twin.interface_contracts import check_contracts

    twin = _twin()
    actor = _actor(request)
    results = await _call(
        "check", project_id, lambda: check_contracts(twin, project_id, key=body.key, actor=actor)
    )
    return ContractCheckResponse(
        project_id=project_id,
        checks=[{"record_id": rid, **ev.model_dump(mode="json")} for ev, rid in results],
    )


@router.post("/projects/{project_id}/contracts/{key}/links", response_model=dict[str, Any])
async def link_contract_item(
    project_id: str, key: str, body: ContractLinkRequest, request: Request
) -> dict[str, Any]:
    """Name the item that implements one side of a contract (a new revision)."""
    from api_gateway.twin.interface_contracts import link_item

    twin = _twin()
    actor = _actor(request)
    stored = await _call(
        "link",
        project_id,
        lambda: link_item(
            twin,
            project_id,
            key,
            role=body.role,
            item_key=body.item_key,
            actor=actor,
            project_backend=_backend(),
        ),
    )
    return {"key": stored.key, "ref": stored.ref, "interface_version": stored.interface_version}


@router.post("/projects/{project_id}/contracts/{key}/agree", response_model=ContractAgreeResponse)
async def agree_contract(
    project_id: str, key: str, body: ContractAgreeRequest, request: Request
) -> ContractAgreeResponse:
    """One owning domain agrees to the contract's current interface version."""
    from api_gateway.twin.interface_contracts import agree

    twin = _twin()
    actor = _actor(request)
    result = await _call(
        "agree",
        project_id,
        lambda: agree(
            twin,
            project_id,
            key,
            domain=body.domain,
            actor=actor,
            rationale=body.rationale,
            project_backend=_backend(),
        ),
    )
    return ContractAgreeResponse(**result)
