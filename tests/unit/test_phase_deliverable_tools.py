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


def test_simulation_phase_can_stage_committed_geometry() -> None:
    """FORGE-504: a committed cad_model is in the blob store, not on disk.

    Live, a simulation phase recorded 'FEA blocked: no meshable STEP path'
    because nothing in its tool set could materialise the committed geometry.
    """
    from mcp_core import profiles

    tools = profiles.tools_for_phase(("mechanical",), ("simulation_result",))
    assert "twin.stage_work_product_file" in tools
    assert "freecad.generate_mesh" in tools


_REAL_CATALOG = [
    "freecad.create_assembly",
    "freecad.add_part_to_assembly",
    "freecad.create_body",
    "cadquery.create_assembly",
    "cadquery.create_parametric",
    "calculix.run_fea",
    "project.delete",
]


def _search_fixture(max_total: int | None = 40) -> tuple[Any, Any]:
    from api_gateway.chat.harness_backend import make_search_tools_tool
    from orchestrator.harness.tools import ToolRegistry

    class Bridge:
        async def list_tools(self) -> list[dict[str, Any]]:
            return [
                {"tool_id": t, "capability": t.split(".")[1].replace("_", " ")}
                for t in _REAL_CATALOG
            ]

        async def invoke(self, tid: str, args: dict[str, Any]) -> Any:
            return {}

    registry = ToolRegistry()
    cell: dict[str, Any] = {"runtime": type("R", (), {"tools": registry})()}
    return make_search_tools_tool(Bridge(), None, cell, max_total_tools=max_total), registry  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_search_finds_freecad_assembly_tools_by_multiword_query() -> None:
    tool, registry = _search_fixture()
    out = await tool.handler({"query": "freecad assembly create assembly"})
    ids = {m["id"] for m in out["matches"]}
    assert "freecad.create_assembly" in ids
    assert all(not i.startswith("cadquery.") for i in ids)
    assert len(out["registered"]) == len(out["matches"]) >= 1
    assert all(m["description"] for m in out["matches"])
    assert {t.name for t in registry.all_tools()} >= set(out["registered"])

    out2 = await tool.handler({"query": "freecad assembly"})
    assert {m["id"] for m in out2["matches"]} == {
        "freecad.create_assembly",
        "freecad.add_part_to_assembly",
    }


@pytest.mark.asyncio
async def test_search_finds_cadquery_assembly() -> None:
    tool, _ = _search_fixture()
    out = await tool.handler({"query": "cadquery assembly"})
    assert [m["id"] for m in out["matches"]] == ["cadquery.create_assembly"]
    assert len(out["registered"]) == 1


@pytest.mark.asyncio
async def test_search_reports_service_refused_tool_as_unavailable() -> None:
    tool, registry = _search_fixture()
    out = await tool.handler({"query": "project delete"})
    assert out["unavailable"] == ["project.delete"]
    assert out["registered"] == []
    assert registry.all_tools() == []
    assert "unavailable" in out["instruction"]


@pytest.mark.asyncio
async def test_search_no_match_says_so() -> None:
    tool, _ = _search_fixture()
    out = await tool.handler({"query": "nonexistent widget"})
    assert out["matches"] == []
    assert "No tool matched" in out["instruction"]
