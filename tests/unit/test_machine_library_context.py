"""Library machines in a flow's context (FORGE-451 step 2, FORGE-611 wiring)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from api_gateway.design_flows.routes import ProposeFlowRequest
from api_gateway.library import routes as library_routes
from orchestrator.design_flow.context import FlowContext, ManufacturingContext
from twin_core.library.machines import SqliteMachineLibrary, summarize_limits

SPEC = "https://example.com/cr-10-spec"


@pytest.fixture
def library() -> Any:
    lib = SqliteMachineLibrary()
    library_routes.init_machine_library(lib)
    yield lib
    library_routes.init_machine_library(None)


def _printer(lib: SqliteMachineLibrary, name: str, status: str = "available") -> str:
    return lib.register(
        {
            "layer": "instance",
            "kind": "fdm_printer",
            "name": name,
            "processes": ["fdm"],
            "route": "in_house",
            "status": status,
            "capabilities": {
                "build_volume_mm": {
                    "value": [300, 300, 400],
                    "unit": "mm",
                    "source_kind": "manufacturer_spec",
                    "source_ref": SPEC,
                },
                "min_wall_mm": {"value": 0.8, "unit": "mm", "source_kind": "user"},
                "max_overhang_deg": {"value": 45, "unit": "deg", "source_kind": "assumption"},
            },
        }
    ).id


def _request(**manufacturing: Any) -> ProposeFlowRequest:
    return ProposeFlowRequest.model_validate(
        {"intent": "a bracket", "manufacturingContext": {"route": "in_house", **manufacturing}}
    )


def test_a_summary_names_the_machine_its_limits_and_its_assumptions(
    library: SqliteMachineLibrary,
) -> None:
    line = summarize_limits(library.limits(_printer(library, "Workshop CR-10")))
    assert line.startswith("Workshop CR-10 [mi_")
    assert "envelope 300x300x400 mm" in line
    assert "wall_thickness >= 0.8" in line
    assert "overhang_angle <= 45" in line
    assert "needs review: max_overhang_deg" in line


def test_chosen_machines_reach_the_generator_and_every_phase(
    library: SqliteMachineLibrary,
) -> None:
    chosen = _printer(library, "Workshop CR-10")
    _printer(library, "Spare printer")
    context = _request(machine_ids=[chosen]).flow_context()
    assert context.manufacturing is not None
    assert context.manufacturing.machine_ids == (chosen,)
    prompt = context.describe_for_prompt()
    assert "Machines chosen from the machine library" in prompt
    assert "Workshop CR-10" in prompt
    assert "Spare printer" not in prompt
    assert "library.machine_limits" in prompt
    assert "Workshop CR-10" in context.render_for_phases()
    assert chosen in context.capability_basis()


def test_with_none_chosen_the_available_ones_are_listed(library: SqliteMachineLibrary) -> None:
    _printer(library, "Workshop CR-10")
    _printer(library, "Broken printer", status="offline")
    prompt = _request().flow_context().describe_for_prompt()
    assert "none chosen for this project" in prompt
    assert "Workshop CR-10" in prompt
    assert "Broken printer" not in prompt


def test_an_unknown_machine_id_is_refused_not_dropped(library: SqliteMachineLibrary) -> None:
    with pytest.raises(HTTPException) as exc:
        _request(machineIds=["mi_typo"]).flow_context()
    assert exc.value.status_code == 422
    assert "mi_typo" in str(exc.value.detail)


def test_an_empty_library_leaves_the_context_text_unchanged(
    library: SqliteMachineLibrary,
) -> None:
    before = FlowContext(
        manufacturing=ManufacturingContext(route=_request().flow_context().route)
    ).describe_for_prompt()
    assert _request().flow_context().describe_for_prompt() == before
    assert "machine library" not in before
