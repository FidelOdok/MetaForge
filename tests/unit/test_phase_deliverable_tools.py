"""FORGE-497: a phase's tool set follows its deliverables, whatever tailoring did."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from api_gateway.runs.flow_brain import _phase_deliverables, _report_phase_tools
from mcp_core.profiles import (
    DELIVERABLE_TOOLS,
    PHASE_COMMON,
    PHASE_MCP_BUDGET,
    deliverable_tools,
    phase_overflow,
    tools_for_phase,
)
from orchestrator.design_flow.generator import (
    DELIVERABLE_CORE_DISCIPLINES,
    Operation,
    OperationKind,
    apply_operations,
    merge_disciplines,
)
from orchestrator.design_flow.spec import FLOWS


def _needs(phase: Any) -> frozenset[str]:
    return deliverable_tools(_phase_deliverables(phase))


def test_every_template_phase_reaches_every_deliverable_tool() -> None:
    for flow in FLOWS.values():
        for phase in flow.phases:
            tools = _report_phase_tools(phase)
            assert _needs(phase) <= tools, (flow.id, phase.id, sorted(_needs(phase) - tools))
            assert PHASE_COMMON <= tools
            assert len(tools) <= PHASE_MCP_BUDGET, (flow.id, phase.id, len(tools))


def test_tailoring_simulation_to_mechanical_keeps_the_fea_tools() -> None:
    for flow in FLOWS.values():
        for phase in flow.phases:
            if "simulation_result" not in _phase_deliverables(phase):
                continue
            ops = [
                Operation(
                    kind=OperationKind.SET_DISCIPLINES,
                    phase_id=phase.id,
                    rationale="live repro",
                    value=["mechanical"],
                )
            ]
            tailored, applied = apply_operations(flow, ops)
            assert applied
            new = next(p for p in tailored.phases if p.id == phase.id)
            assert "simulation" in new.disciplines and "mechanical" in new.disciplines
            tools = _report_phase_tools(new)
            for needed in ("freecad.generate_mesh", "calculix.run_fea"):
                assert needed in tools, (flow.id, phase.id)
            # Even a phase whose disciplines were bypassed entirely keeps them.
            bare = replace(phase, disciplines=("mechanical",))
            assert _needs(bare) <= _report_phase_tools(bare)


def test_set_disciplines_widens_but_never_drops_the_core_discipline() -> None:
    sim = next(
        p for f in FLOWS.values() for p in f.phases if "simulation_result" in _phase_deliverables(p)
    )
    merged = merge_disciplines(sim, ["electronics"])
    assert "simulation" in merged and "electronics" in merged
    assert set(DELIVERABLE_CORE_DISCIPLINES.values()) >= {"simulation", "mechanical"}


def test_deliverable_tools_are_never_dropped_and_discipline_tools_go_first() -> None:
    deliverables = ("simulation_result", "bom")
    must = PHASE_COMMON | deliverable_tools(deliverables)
    tools = tools_for_phase(("mechanical", "simulation", "electronics", "robotics"), deliverables)
    assert must <= tools
    dropped = phase_overflow(("mechanical", "simulation", "electronics", "robotics"), deliverables)
    assert not set(dropped) & must
    assert not set(dropped) & tools


def test_service_refused_tools_get_no_slot() -> None:
    tools = tools_for_phase(("robotics", "electronics", "mechanical"), ("robot_description",))
    for refused in ("cadquery.export_urdf", "kicad.export_bom", "freecad.export_geometry"):
        assert refused not in tools


def test_deliverable_table_names_real_types() -> None:
    for tools in DELIVERABLE_TOOLS.values():
        assert tools


@pytest.mark.asyncio
async def test_search_tools_says_unavailable_when_phase_is_at_cap() -> None:
    from api_gateway.chat.harness_backend import make_search_tools_tool
    from orchestrator.harness.tools import ToolRegistry

    class Bridge:
        async def list_tools(self) -> list[dict[str, Any]]:
            return [{"tool_id": "calculix.run_fea", "capability": "run fea"}]

        async def invoke(self, tid: str, args: dict[str, Any]) -> Any:
            return {}

    registry = ToolRegistry()
    registry.register_mcp(
        "twin", "x", description="x", input_schema={"type": "object"}, handler=_noop
    )
    cell: dict[str, Any] = {"runtime": type("R", (), {"tools": registry})()}
    tool = make_search_tools_tool(Bridge(), None, cell, max_total_tools=1)  # type: ignore[arg-type]
    out = await tool.handler({"query": "fea"})
    assert out["registered"] == []
    assert out["not_registered"]
    assert "NOT available to this phase" in out["instruction"]
    assert "EXIST" in out["instruction"]


async def _noop(arguments: dict[str, Any]) -> Any:
    return {}


def test_must_keep_tools_over_budget_raise_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_core import profiles

    monkeypatch.setattr(profiles, "PHASE_MCP_BUDGET", 5)
    with pytest.raises(profiles.PhaseToolBudgetError, match="phase budget"):
        profiles.tools_for_phase((), ("cad_model",))


def test_cad_model_plus_simulation_result_exceeds_the_budget_today() -> None:
    from mcp_core import profiles

    with pytest.raises(profiles.PhaseToolBudgetError):
        profiles.tools_for_phase((), ("cad_model", "simulation_result"))
