"""Skill-specific tests for run_cfd (FORGE-543, FORGE-557)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.simulation.skills.run_cfd.handler import RunCfdHandler
from domain_agents.simulation.skills.run_cfd.schema import InternalFlowCase, RunCfdInput
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

CASE = {
    "mesh_file": "m.inp",
    "material": {"name": "aluminium_6061"},
    "heat_source_node_set": "Surface2",
    "power_dissipation_w": 3.0,
    "sink_node_set": "Surface1",
    "sink_temp_c": 40.0,
}

FLOW = {
    "inlet_stl": "duct/inlet.stl",
    "outlet_stl": "duct/outlet.stl",
    "wall_stls": ["duct/wall.stl"],
    "stl_units": "mm",
    "inside_point": [100.0, 0.0, 0.0],
    "fluid": {"density_kg_m3": 1.2, "kinematic_viscosity_m2_s": 1.5e-5},
    "inlet_velocity_m_s": 2.0,
    "turbulence": "laminar",
}

FLOW_RESULT = {
    "completed": True,
    "converged": True,
    "solution_stable": True,
    "pressure_drop_pa": 0.42,
    "inlet": {"volumetric_flow_m3_s": 6.2e-4},
    "mesh": {"cells": 13784},
    "case_dir": "/workspace/openfoam/abc/case",
    "warnings": [],
}


@pytest.fixture()
def cfd_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


class TestConduction:
    async def test_conduction_only(self, cfd_context: SkillContext) -> None:
        cfd_context.mcp.register_tool("calculix.run_thermal", "thermal_analysis")
        cfd_context.mcp.register_tool_response(
            "calculix.run_thermal", {"max_temperature_c": 47.2, "min_temperature_c": 40.0}
        )
        out = await RunCfdHandler(cfd_context).execute(
            RunCfdInput(work_product_id=uuid4(), conduction=CASE)
        )
        assert out.analysis == "conduction_only"
        assert out.max_temperature_c == 47.2
        assert out.max_velocity_ms is None and out.pressure_drop_pa is None

    async def test_free_form_flow_input_is_refused(self, cfd_context: SkillContext) -> None:
        with pytest.raises(ValueError, match="internal_flow"):
            await RunCfdHandler(cfd_context).execute(
                RunCfdInput(
                    work_product_id=uuid4(),
                    conduction=CASE,
                    boundary_conditions={"inlet_velocity_ms": 2.0},
                )
            )

    async def test_no_analysis_is_refused_with_both_options_named(
        self, cfd_context: SkillContext
    ) -> None:
        with pytest.raises(ValueError, match="conduction.*internal_flow"):
            await RunCfdHandler(cfd_context).execute(RunCfdInput(work_product_id=uuid4()))

    async def test_two_analyses_in_one_call_are_refused(self, cfd_context: SkillContext) -> None:
        with pytest.raises(ValueError, match="not both"):
            await RunCfdHandler(cfd_context).execute(
                RunCfdInput(work_product_id=uuid4(), conduction=CASE, internal_flow=FLOW)
            )


class TestInternalFlow:
    async def test_a_converged_run_returns_the_pressure_drop(
        self, cfd_context: SkillContext
    ) -> None:
        cfd_context.mcp.register_tool("openfoam.run_internal_flow", "flow_simulation")
        cfd_context.mcp.register_tool_response("openfoam.run_internal_flow", FLOW_RESULT)
        out = await RunCfdHandler(cfd_context).execute(
            RunCfdInput(work_product_id=uuid4(), internal_flow=FLOW)
        )
        assert out.analysis == "internal_flow"
        assert out.pressure_drop_pa == 0.42
        assert out.volumetric_flow_m3_s == 6.2e-4
        assert out.converged is True and out.solution_stable is True
        assert out.mesh_cells == 13784
        assert out.solver == "openfoam.run_internal_flow"
        assert out.max_temperature_c is None
        assert out.warnings == []

    async def test_the_case_is_sent_to_the_solver_unchanged(
        self, cfd_context: SkillContext
    ) -> None:
        cfd_context.mcp.register_tool("openfoam.run_internal_flow", "flow_simulation")
        cfd_context.mcp.register_tool_response("openfoam.run_internal_flow", FLOW_RESULT)
        calls: list[tuple[str, dict]] = []
        original = cfd_context.mcp.invoke

        async def spy(tool_id: str, arguments: dict, **kw: object) -> dict:
            calls.append((tool_id, arguments))
            return await original(tool_id, arguments, **kw)

        cfd_context.mcp.invoke = spy  # type: ignore[method-assign]
        await RunCfdHandler(cfd_context).execute(
            RunCfdInput(work_product_id=uuid4(), internal_flow=FLOW)
        )
        assert calls[-1][0] == "openfoam.run_internal_flow"
        sent = calls[-1][1]
        assert sent["inlet_stl"] == "duct/inlet.stl" and sent["stl_units"] == "mm"
        assert sent["fluid"]["kinematic_viscosity_m2_s"] == 1.5e-5

    async def test_a_run_that_did_not_converge_says_so(self, cfd_context: SkillContext) -> None:
        cfd_context.mcp.register_tool("openfoam.run_internal_flow", "flow_simulation")
        cfd_context.mcp.register_tool_response(
            "openfoam.run_internal_flow",
            {**FLOW_RESULT, "converged": False, "solution_stable": False},
        )
        out = await RunCfdHandler(cfd_context).execute(
            RunCfdInput(work_product_id=uuid4(), internal_flow=FLOW)
        )
        assert out.converged is False
        assert out.warnings[0].startswith("not converged")
        assert "still moving" in out.warnings[0]

    async def test_a_failed_run_raises_with_the_stage_and_reason(
        self, cfd_context: SkillContext
    ) -> None:
        cfd_context.mcp.register_tool("openfoam.run_internal_flow", "flow_simulation")
        cfd_context.mcp.register_tool_response(
            "openfoam.run_internal_flow",
            {
                "completed": False,
                "stage_failed": "snappyHexMesh",
                "error": "no mesh faces on outlet: the surfaces did not bound the fluid",
            },
        )
        with pytest.raises(ValueError, match=r"snappyHexMesh.*no mesh faces on outlet"):
            await RunCfdHandler(cfd_context).execute(
                RunCfdInput(work_product_id=uuid4(), internal_flow=FLOW)
            )

    async def test_the_precondition_checks_the_flow_tool_not_the_thermal_one(
        self, cfd_context: SkillContext
    ) -> None:
        cfd_context.mcp.register_tool("calculix.run_thermal", "thermal_analysis")
        errors = await RunCfdHandler(cfd_context).validate_preconditions(
            RunCfdInput(work_product_id=uuid4(), internal_flow=FLOW)
        )
        assert "openfoam.run_internal_flow is not available" in errors

    def test_a_turbulent_case_needs_its_inlet_values(self) -> None:
        with pytest.raises(ValueError, match="turbulence_intensity"):
            InternalFlowCase.model_validate({**FLOW, "turbulence": "kOmegaSST"})

    def test_the_fluid_is_required(self) -> None:
        without = {k: v for k, v in FLOW.items() if k != "fluid"}
        with pytest.raises(ValueError):
            InternalFlowCase.model_validate(without)


class TestMirrorsTheAdapter:
    """This package may not import ``tool_registry``, so the flow case is written out
    here. If the adapter's request changes and this does not, the skill sends a call
    the adapter refuses; this fails first."""

    def test_the_fields_match_the_adapters_request_model(self) -> None:
        from tool_registry.tools.openfoam.case_builder import FlowRequest

        mine, theirs = InternalFlowCase.model_json_schema(), FlowRequest.model_json_schema()
        assert set(mine["properties"]) == set(theirs["properties"])
        assert set(mine["required"]) == set(theirs["required"])
        for sub in ("FlowFluid", "FlowMesh", "FlowSolver"):
            pass  # the nested models are named differently; compare their fields below
        assert set(mine["$defs"]["FlowFluid"]["properties"]) == set(
            theirs["$defs"]["Fluid"]["properties"]
        )
        assert set(mine["$defs"]["FlowMesh"]["properties"]) == set(
            theirs["$defs"]["MeshSettings"]["properties"]
        )
        assert set(mine["$defs"]["FlowSolver"]["properties"]) == set(
            theirs["$defs"]["SolverSettings"]["properties"]
        )
