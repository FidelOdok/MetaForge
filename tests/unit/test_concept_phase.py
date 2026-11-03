"""The concept phase in mech_v1, and sketches as intent only (FORGE-757, FORGE-759).

The Chair run went from the process choice straight to CAD: its structure and
joints were never decided as a concept, and surfaced as a DFM failure and four
design attempts. mech_v1 now decides the concept first, with labelled sketches
and a selected assembly, and a sketch can never stand as engineering evidence.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from api_gateway.twin.claim_recorder import is_illustrative, make_claim_recorder
from api_gateway.twin.design_sketch_recorder import make_design_sketch_recorder
from orchestrator.design_flow.templates import load_templates
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct

SVG = "<svg xmlns='http://www.w3.org/2000/svg'><text>Chair Seat (intended)</text></svg>"


def _phases() -> list[Any]:
    return list(load_templates()["mech_v1"].definition.phases)


def test_the_concept_sits_between_the_process_choice_and_industrial_design() -> None:
    ids = [p.id for p in _phases()]
    assert ids.index("route_selection") + 1 == ids.index("concept_selection")
    assert ids.index("concept_selection") + 1 == ids.index("industrial_design")
    assert ids.index("concept_selection") < ids.index("design")


def test_the_concept_needs_a_sketch_and_a_decision_and_authors_no_cad() -> None:
    concept = next(p for p in _phases() if p.id == "concept_selection")
    assert set(concept.required_deliverables) == {"design_sketch", "design_decision"}
    assert concept.gate is not None and concept.gate.gate_id == "G5"
    assert "Do not author CAD" in concept.objective
    assert "intended, assumption or open" in concept.objective


@pytest.mark.asyncio
async def test_a_committed_sketch_is_marked_illustrative() -> None:
    twin = InMemoryTwinAPI.create()
    commit = make_design_sketch_recorder(twin)
    out = await commit(name="Concept A: four-leg frame", html_content=SVG)
    from uuid import UUID

    node_id = out.get("node_id") or out.get("id") or out.get("work_product_id")
    wp = await twin.get_work_product(UUID(str(node_id)))
    assert wp.metadata["authority"] == "illustrative"
    assert is_illustrative(wp)


PROJECT = str(uuid4())


async def _requirement(twin: InMemoryTwinAPI) -> str:
    from uuid import UUID

    c = await twin.create_constraint(
        Constraint(
            name="seat_height_max",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
            project_id=UUID(PROJECT),
        )
    )
    return c.name


async def _work_product(twin: InMemoryTwinAPI, wp_type: WorkProductType, name: str) -> str:
    from uuid import UUID

    wp = await twin.create_work_product(
        WorkProduct(
            name=name,
            type=wp_type,
            domain="mechanical",
            file_path=f"{name}.x",
            content_hash="h",
            format="html" if wp_type is WorkProductType.DESIGN_SKETCH else "step",
            created_by="user",
            project_id=UUID(PROJECT),
        )
    )
    return wp.name


@pytest.mark.asyncio
async def test_a_sketch_cannot_be_claimed_to_satisfy_a_requirement() -> None:
    twin = InMemoryTwinAPI.create()
    requirement = await _requirement(twin)
    sketch = await _work_product(twin, WorkProductType.DESIGN_SKETCH, "Concept A sketch")
    record = make_claim_recorder(twin)
    with pytest.raises(ValueError, match="illustrative"):
        await record(requirement_ref=requirement, artefact_ref=sketch, project_id=PROJECT)


@pytest.mark.asyncio
async def test_a_cad_model_still_can() -> None:
    twin = InMemoryTwinAPI.create()
    requirement = await _requirement(twin)
    cad = await _work_product(twin, WorkProductType.CAD_MODEL, "Chair Seat")
    out = await make_claim_recorder(twin)(
        requirement_ref=requirement, artefact_ref=cad, project_id=PROJECT
    )
    assert out
