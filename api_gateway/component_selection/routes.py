"""Requirement-driven component selection API (FORGE-265, gap G-C1).

``POST /v1/component-selection/select`` is a thin REST wrapper over
``api_gateway.twin.component_selection.make_component_selector`` -- same
precedent as ``POST /v1/trade-study/select``, so the dashboard's BOM-page
"Select component" action doesn't need an MCP client of its own, and reuses
the SAME bound callable ``twin.select_component`` uses (recording isn't
duplicated between the MCP tool and this route).

Unlike ``trade_study``'s options, there is no ``GET`` here: a candidate is
just an mpn + caller-asserted specs supplied inline at selection time, not a
pre-recorded graph entity to list -- see ``component_selection.py``'s own
module docstring for why (a servo's published torque rating is typed in at
comparison time, not persisted as a browsable node beforehand).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

_component_selector: Any = None


def init_component_selector(selector: Any) -> None:
    global _component_selector  # noqa: PLW0603
    _component_selector = selector


router = APIRouter(prefix="/v1/component-selection", tags=["component-selection"])


class RequiredSpec(BaseModel):
    op: str
    value: float


class Candidate(BaseModel):
    mpn: str
    manufacturer: str
    specs: dict[str, float] = {}


class SelectComponentRequest(BaseModel):
    candidates: list[Candidate]
    requiredSpecs: dict[str, RequiredSpec]  # noqa: N815 -- dashboard contract is camelCase
    selectedMpn: str  # noqa: N815
    category: str
    purchaseUnit: str  # noqa: N815
    title: str
    rationale: str
    quantity: int = 1
    projectId: str | None = None  # noqa: N815
    requirementIds: list[str] | None = None  # noqa: N815


@router.post("/select")
async def select_component(payload: SelectComponentRequest) -> dict[str, Any]:
    if _component_selector is None:
        raise HTTPException(status_code=503, detail="component selection is not available")
    try:
        return await _component_selector(
            candidates=[c.model_dump() for c in payload.candidates],
            required_specs={k: v.model_dump() for k, v in payload.requiredSpecs.items()},
            selected_mpn=payload.selectedMpn,
            category=payload.category,
            purchase_unit=payload.purchaseUnit,
            title=payload.title,
            rationale=payload.rationale,
            quantity=payload.quantity,
            project_id=payload.projectId,
            requirement_ids=payload.requirementIds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
