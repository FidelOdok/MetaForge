"""A concept records its selected assembly and candidate parts as data (FORGE-758).

The Chair run's concept was prose: nothing said which parts met where or how
they were held, and the joints surfaced as design-gate failures. The concept
decision now carries the assembly, the concepts it was chosen from, and the
standard parts its joints rely on as candidates.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.twin.component_recorder import make_component_recorder
from api_gateway.twin.decision_recorder import make_decision_recorder
from orchestrator.design_flow.context import parse_concept_assembly, parse_concept_options
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.gates import evaluate_g5_concept_selection

ASSEMBLY: dict[str, Any] = {
    "parts": [
        {"name": "Seat", "material": "birch plywood 18 mm", "process": "cnc_routing"},
        {"name": "Leg", "quantity": 4, "provenance": "assumption"},
        {"name": "Dowel 8x40", "source": "standard", "quantity": 8},
    ],
    "connections": [
        {"parts": ["Seat", "Leg"], "joint": "dowel", "fasteners": "2 x Dowel 8x40 per leg"},
    ],
}

OPTIONS: list[dict[str, Any]] = [
    {"name": "Four-leg dowelled", "summary": "Flat-pack legs dowelled under the seat",
     "selected": True},
    {"name": "Slot-together", "summary": "Interlocking sheets, no fixings"},
]  # fmt: skip


def test_an_assembly_with_joints_parses() -> None:
    assembly = parse_concept_assembly(ASSEMBLY)
    assert [p.name for p in assembly.parts] == ["Seat", "Leg", "Dowel 8x40"]
    assert assembly.parts[1].provenance == "assumption"
    assert assembly.parts[2].source == "standard"
    assert assembly.connections[0].joint == "dowel"
    assert "Seat to Leg: dowel" in assembly.summary()
    assert "| Seat to Leg | dowel |" in assembly.markdown()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"parts": []}, "at least one part"),
        ({"parts": [{"name": "A"}, {"name": "A"}]}, "unique"),
        ({"parts": [{"name": "A", "source": "magic"}]}, "source"),
        ({"parts": [{"name": "A", "provenance": "certain"}]}, "provenance"),
        ({"parts": [{"name": "A"}], "connections": [{"parts": ["A", "B"], "joint": "x"}]},
         "not in assembly.parts"),
        ({"parts": [{"name": "A"}, {"name": "B"}], "connections": [{"parts": ["A", "B"]}]},
         "needs its joint"),
    ],
)  # fmt: skip
def test_a_malformed_assembly_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_concept_assembly({**ASSEMBLY, **change})


def test_exactly_one_concept_is_selected() -> None:
    assert [o.selected for o in parse_concept_options(OPTIONS)] == [True, False]
    with pytest.raises(ValueError, match="exactly one"):
        parse_concept_options([{**o, "selected": True} for o in OPTIONS])
    with pytest.raises(ValueError, match="exactly one"):
        parse_concept_options([{**o, "selected": False} for o in OPTIONS])


@pytest.mark.asyncio
async def test_the_decision_carries_the_assembly_and_the_gate_reads_it() -> None:
    twin = InMemoryTwinAPI.create()
    project = str(uuid4())
    out = await make_decision_recorder(twin)(
        title="Concept: four-leg dowelled chair",
        rationale="Fits the CNC route; dowels are cheap and self-locating",
        alternatives=[{"option": "Slot-together", "reason_rejected": "racks under load"}],
        assembly=ASSEMBLY,
        concept_options=OPTIONS,
        project_id=project,
    )
    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.metadata["assembly"]["connections"][0]["joint"] == "dowel"
    assert wp.metadata["concept_options"][0]["selected"] is True

    gate = await evaluate_g5_concept_selection(twin, UUID(project))
    check = next(c for c in gate.checks if c.id == "concept:selected_assembly")
    assert check.status.value == "pass"


@pytest.mark.asyncio
async def test_a_decision_with_a_bad_assembly_writes_nothing() -> None:
    twin = InMemoryTwinAPI.create()
    with pytest.raises(ValueError, match="twin.record_decision: assembly"):
        await make_decision_recorder(twin)(title="Concept", rationale="r", assembly={"parts": []})
    assert await twin.list_work_products() == []


@pytest.mark.asyncio
async def test_without_an_assembly_the_check_is_not_evaluated() -> None:
    twin = InMemoryTwinAPI.create()
    project = str(uuid4())
    await make_decision_recorder(twin)(title="Arch", rationale="r", project_id=project)
    gate = await evaluate_g5_concept_selection(twin, UUID(project))
    check = next(c for c in gate.checks if c.id == "concept:selected_assembly")
    assert check.status.value == "not_evaluated"


@pytest.mark.asyncio
async def test_a_part_the_concept_relies_on_is_a_candidate() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_component_recorder(twin)
    out = await record(
        mpn="DOWEL-8x40",
        manufacturer="Generic",
        category="fastener",
        purchase_unit="discrete_part",
        quantity=8,
        selection="candidate",
    )
    items = await twin.list_bom_items()
    assert items[0].specifications["selection"] == "candidate"
    assert out

    from api_gateway.bom.routes import _item_to_component

    assert _item_to_component(items[0]).selection == "candidate"
    with pytest.raises(ValueError, match="selection"):
        await record(
            mpn="X", manufacturer="Y", category="z", purchase_unit="discrete_part",
            selection="maybe",
        )  # fmt: skip
