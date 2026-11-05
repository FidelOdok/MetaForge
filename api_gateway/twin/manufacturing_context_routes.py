"""Manufacturing context routes.

Under ``/v1/twin/projects/{project_id}/manufacturing-context`` (``?component=``
scopes a call to one component; without it, the product as a whole):

- ``GET``: the current context, its ref (``MFC-...@n`` or ``unresolved``), its
  gaps, the note a concept made now would carry and the block a generator
  would be given. A component with no record of its own shows the product's.
- ``PUT``: state fields. A person's values are ``user_stated``; ``null``
  clears a field. An agent caller (``X-MetaForge-Surface: agent``) can only
  propose: its values are assumptions, and the fields that are never assumed
  (target cost, lead time, machine or supplier) are refused. Every change is
  the item's next revision; the conflict rule's refusals are returned.
- ``GET .../history``: every revision, with what each changed.
- ``POST .../propose``: candidate processes and materials as assumptions.
  Writes nothing.

The logic is in :mod:`api_gateway.twin.manufacturing_context`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api_gateway.twin.change_classification import ChangeGuardRefusedError
from api_gateway.twin.manufacturing_context import (
    ContextView,
    ManufacturingContextError,
    ProposalView,
    context_history,
    effective_context,
    parse_cost,
    parse_quantity,
    propose,
    view_of,
    write_context,
)
from observability.tracing import get_tracer
from twin_core.manufacturing import Authority, ContextField, Provenance
from twin_core.manufacturing.context import CONTEXT_FIELDS, NEVER_ASSUMED

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.manufacturing_context_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

_BASE = "/projects/{project_id}/manufacturing-context"
SURFACE_HEADER = "X-MetaForge-Surface"

T = TypeVar("T")


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _check_project_id(project_id: str) -> None:
    from uuid import UUID

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None


async def _call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run one call, mapping refusals to 4xx and anything else to 502."""
    from twin_core.items import ItemError, ItemRevisionConflictError

    _check_project_id(project_id)
    with tracer.start_as_current_span(f"twin.manufacturing_context_routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await fn()
        except ManufacturingContextError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ChangeGuardRefusedError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ItemRevisionConflictError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ItemError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning(
                "manufacturing_context_route_failed",
                route=name,
                project_id=project_id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=502, detail="The manufacturing context could not be read or changed"
            ) from exc


class ContextPutRequest(BaseModel):
    """Fields to state. ``null`` clears a field; a field left out is unchanged."""

    fields: dict[str, Any] = Field(default_factory=dict)
    #: The revision this edit was made against; 409 when the head moved on.
    expected_revision: int | None = Field(None, ge=0)
    change_reason: str = Field("", max_length=300)
    #: Per-field notes (why it was stated), keyed like ``fields``.
    notes: dict[str, str] = Field(default_factory=dict)


class ProposeRequest(BaseModel):
    """What to propose from, on top of what the record already says."""

    text: str = Field("", max_length=1500)


def normalize_value(name: str, value: Any) -> Any:
    """A field value in the shape the record keeps, from what a client sent."""
    if value is None:
        return None
    if name in ("process_candidates", "material_candidates"):
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",")]
        if not isinstance(value, list):
            raise ValueError(f"{name} is a list of names")
        return [str(v).strip()[:120] for v in value if str(v).strip()][:10]
    if name == "target_cost":
        if isinstance(value, str):
            return parse_cost(value)
        if isinstance(value, int | float):
            raise ValueError("target_cost needs its currency: {amount, currency}")
        if isinstance(value, dict):
            amount = value.get("amount")
            return {
                "amount": float(amount) if isinstance(amount, int | float) else None,
                "currency": str(value.get("currency") or "").strip().upper()[:8],
                "text": str(value.get("text") or "")[:200],
            }
        raise ValueError("target_cost is {amount, currency, text} or text")
    if name == "lead_time":
        if isinstance(value, int | float):
            return {"days": float(value), "text": ""}
        if isinstance(value, str):
            return {"days": None, "text": value.strip()[:200]}
        if isinstance(value, dict):
            days = value.get("days")
            return {
                "days": float(days) if isinstance(days, int | float) else None,
                "text": str(value.get("text") or "")[:200],
            }
        raise ValueError("lead_time is a number of days, text, or {days, text}")
    if name == "quantity":
        if isinstance(value, bool):
            raise ValueError("quantity is a count")
        if isinstance(value, int):
            if value < 1:
                raise ValueError("quantity is at least 1")
            return value
        return parse_quantity(str(value))
    if name == "machine_or_supplier_profile":
        if isinstance(value, str):
            return {"description": value.strip()[:300]}
        if isinstance(value, dict):
            return {
                k: str(v).strip()[:300]
                for k, v in value.items()
                if k in ("machine", "machine_id", "supplier", "description") and str(v).strip()
            }
        raise ValueError("machine_or_supplier_profile is {machine, machine_id, supplier} or text")
    if isinstance(value, str):
        return value.strip()[:500]
    if isinstance(value, int | float | list | dict):
        return value
    raise ValueError(f"{name}: unsupported value")


def _actor(request: Request) -> str:
    from api_gateway.auth.approver import approver_from_request

    return str(approver_from_request(request).actor_id)


def updates_from_put(
    body: ContextPutRequest, *, agent: bool, actor: str
) -> tuple[dict[str, ContextField], bool]:
    """``(updates, clears)`` from a PUT body, or ``ValueError`` naming the bad field."""
    unknown = sorted(set(body.fields) - set(CONTEXT_FIELDS))
    if unknown:
        raise ValueError(f"unknown manufacturing context fields: {', '.join(unknown)}")
    updates: dict[str, ContextField] = {}
    clears = False
    source = "api_agent" if agent else "api"
    for name, raw in body.fields.items():
        value = normalize_value(name, raw)
        note = str(body.notes.get(name) or "")[:500]
        prov = Provenance(source=source, actor=actor)
        if value is None or value in ("", [], {}):
            if agent:
                raise ValueError(f"{name}: clearing a field is a person's statement")
            updates[name] = ContextField(authority=Authority.UNKNOWN, provenance=prov, note=note)
            clears = True
            continue
        if agent and name in NEVER_ASSUMED:
            raise ValueError(
                f"{name} is never assumed: only a person or a verified integration states it"
            )
        updates[name] = ContextField(
            value=value,
            authority=Authority.ASSUMPTION if agent else Authority.USER_STATED,
            provenance=prov,
            note=note,
            # An assumed print orientation only holds for the process it was assumed for.
            process_specific=agent and name == "print_orientation",
        )
    return updates, clears


@router.get(_BASE, response_model=ContextView)
async def get_manufacturing_context(
    project_id: str, component: str | None = Query(None, max_length=120)
) -> ContextView:
    """The current manufacturing context. ``recorded: false`` and ref ``unresolved`` when none."""

    async def run() -> ContextView:
        stored = await effective_context(_twin(), project_id, component)
        return view_of(project_id, component, stored)

    return await _call("get", project_id, run)


@router.put(_BASE, response_model=ContextView)
async def put_manufacturing_context(
    project_id: str,
    body: ContextPutRequest,
    request: Request,
    component: str | None = Query(None, max_length=120),
) -> ContextView:
    """State manufacturing context fields, as the context's next revision.

    A person's values are ``user_stated`` and win over assumptions and over
    older statements or decisions; ``null`` clears a field. An agent caller
    only proposes: its values are assumptions, which never override a stated
    value or a decision. Nothing changed means no new revision.
    """
    agent = (request.headers.get(SURFACE_HEADER) or "").strip().lower() == "agent"
    actor = _actor(request)

    async def run() -> ContextView:
        updates, clears = updates_from_put(body, agent=agent, actor=actor)
        if not updates:
            raise ManufacturingContextError("no fields to state", 422)
        outcome = await write_context(
            _twin(),
            project_id,
            updates,
            source="api_agent" if agent else "api",
            actor=actor,
            change_reason=body.change_reason or "manufacturing context stated",
            component_key=component,
            allow_clear=clears,
            expected_revision=body.expected_revision,
        )
        stored = outcome.stored or await effective_context(_twin(), project_id, component)
        return view_of(project_id, component, stored, outcome=outcome)

    return await _call("put", project_id, run)


@router.get(f"{_BASE}/history")
async def get_manufacturing_context_history(
    project_id: str, component: str | None = Query(None, max_length=120)
) -> dict[str, Any]:
    """Every revision of the context, oldest first, with what each one changed."""

    async def run() -> dict[str, Any]:
        rows = await context_history(_twin(), project_id, component)
        return {"project_id": project_id, "component_key": component, "revisions": rows}

    return await _call("history", project_id, run)


@router.post(f"{_BASE}/propose", response_model=ProposalView)
async def propose_manufacturing_context(
    project_id: str,
    body: ProposeRequest | None = None,
    component: str | None = Query(None, max_length=120),
) -> ProposalView:
    """Candidate processes and materials, as assumptions. Writes nothing.

    Proposed from the intended use, the envelope and the product text, then
    checked against the machine library and ``rank_manufacturing_processes``.
    Always at least two candidates, so no method is locked in silently. To
    accept one, state it with ``PUT``.
    """
    text = body.text if body is not None else ""
    return await _call(
        "propose", project_id, lambda: propose(_twin(), project_id, component, text=text)
    )
