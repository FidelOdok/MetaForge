"""Tests for the CalculiX MCP tool adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.calculix.adapter import CalculixServer
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.result_parser import FrdParseError
from tool_registry.tools.calculix.solver import SolverError

# Same real ccx 2.20 static-stress .frd capture as
# test_calculix_result_parser.py's REAL_CCX_FRD (a single C3D8 cube, 8 nodes,
# nodes 1-4 fixed, nodes 5-8 loaded) -- duplicated here rather than imported
# so this file's fixtures stay self-contained.
REAL_CCX_FRD = """\
    1C
    1UMAT    1STEEL
    2C                             8                                     1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E+01 0.00000E+00 0.00000E+00
 -1         3 1.00000E+01 1.00000E+01 0.00000E+00
 -1         4 0.00000E+00 1.00000E+01 0.00000E+00
 -1         5 0.00000E+00 0.00000E+00 1.00000E+01
 -1         6 1.00000E+01 0.00000E+00 1.00000E+01
 -1         7 1.00000E+01 1.00000E+01 1.00000E+01
 -1         8 0.00000E+00 1.00000E+01 1.00000E+01
 -3
    1PSTEP                         1           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -5  D2          1    2    2    0
 -5  D3          1    2    3    0
 -5  ALL         1    2    0    0    1ALL
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 0.00000E+00 0.00000E+00 0.00000E+00
 -1         3 0.00000E+00 0.00000E+00 0.00000E+00
 -1         4 0.00000E+00 0.00000E+00 0.00000E+00
 -1         5-3.71429E-04-3.71429E-04-1.73333E-03
 -1         6 3.71429E-04-3.71429E-04-1.73333E-03
 -1         7 3.71429E-04 3.71429E-04-1.73333E-03
 -1         8-3.71429E-04 3.71429E-04-1.73333E-03
 -3
    1PSTEP                         2           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -5  SYY         1    4    2    2
 -5  SZZ         1    4    3    3
 -5  SXY         1    4    1    2
 -5  SYZ         1    4    2    3
 -5  SZX         1    4    3    1
 -1         1-2.09997E+01-2.09997E+01-4.89983E+01 1.48489E-16-2.99998E+00-2.99998E+00
 -1         2-2.09997E+01-2.09997E+01-4.89983E+01-3.23572E-17-2.99998E+00 2.99998E+00
 -1         3-2.09997E+01-2.09997E+01-4.89983E+01 7.29624E-17 2.99998E+00 2.99998E+00
 -1         4-2.09997E+01-2.09997E+01-4.89983E+01 2.84439E-17 2.99998E+00-2.99998E+00
 -1         5 9.00015E+00 9.00015E+00-3.09985E+01 1.32215E-16-2.99998E+00-2.99998E+00
 -1         6 9.00015E+00 9.00015E+00-3.09985E+01 3.05009E-15-2.99998E+00 2.99998E+00
 -1         7 9.00015E+00 9.00015E+00-3.09985E+01 4.05887E-15 2.99998E+00 2.99998E+00
 -1         8 9.00015E+00 9.00015E+00-3.09985E+01 1.98207E-15 2.99998E+00-2.99998E+00
 -3
 9999
"""

REAL_CCX_THERMAL_FRD = """\
    1C
    1UMAT    1STEEL
    2C                             8                                     1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E+01 0.00000E+00 0.00000E+00
 -3
    1PSTEP                         1           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  NDTEMP      1    1
 -5  T           1    1    0    0
 -1         1 2.00000E+01
 -1         2 2.00000E+01
 -1         3 2.00000E+01
 -1         4 2.00000E+01
 -1         5 1.00000E+02
 -1         6 1.00000E+02
 -1         7 1.00000E+02
 -1         8 1.00000E+02
 -3
 9999
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> CalculixServer:
    """Bare CalculiX server (no mocks on solver methods)."""
    return CalculixServer()


@pytest.fixture()
def server_with_mocks() -> CalculixServer:
    """Server with mocked solver methods for testing."""
    s = CalculixServer()
    s._execute_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "max_von_mises": {"bracket_body": 145.2, "bracket_mount": 89.7},
            "solver_time": 12.5,
            "mesh_elements": 45000,
        }
    )
    s._execute_thermal_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "max_temperature_c": 85.3,
            "min_temperature_c": 22.1,
            "solver_time": 8.2,
        }
    )
    s._validate_mesh_file = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "valid": True,
            "element_count": 45000,
            "node_count": 12000,
            "max_aspect_ratio": 3.2,
            "issues": [],
        }
    )
    return s


# ---------------------------------------------------------------------------
# TestCalculixConfig
# ---------------------------------------------------------------------------


class TestCalculixConfig:
    def test_default_config(self) -> None:
        cfg = CalculixConfig()
        assert cfg.ccx_binary == "ccx"
        assert cfg.work_dir == "/tmp/calculix"
        assert cfg.max_solve_time == 600
        assert cfg.max_memory_mb == 2048
        assert cfg.supported_analysis_types == ["static_stress", "thermal", "modal"]

    def test_custom_config(self) -> None:
        cfg = CalculixConfig(
            ccx_binary="/usr/local/bin/ccx",
            work_dir="/data/calculix",
            max_solve_time=300,
            max_memory_mb=4096,
            supported_analysis_types=["static_stress"],
        )
        assert cfg.ccx_binary == "/usr/local/bin/ccx"
        assert cfg.work_dir == "/data/calculix"
        assert cfg.max_solve_time == 300
        assert cfg.max_memory_mb == 4096
        assert cfg.supported_analysis_types == ["static_stress"]


# ---------------------------------------------------------------------------
# TestCalculixServer
# ---------------------------------------------------------------------------


class TestCalculixServer:
    def test_server_registers_nine_tools(self, server: CalculixServer) -> None:
        # FORGE-280 adds cross_check_cantilever_beam + check_mesh_convergence.
        # FORGE-281 adds cross_check_cantilever_frequency.
        # FORGE-283 adds compute_joint_loads.
        # FORGE-282 adds cross_check_thermal_steady_state.
        assert len(server.tool_ids) == 9

    def test_tool_ids(self, server: CalculixServer) -> None:
        expected = {
            "calculix.run_fea",
            "calculix.run_thermal",
            "calculix.validate_mesh",
            "calculix.extract_results",
            "calculix.cross_check_cantilever_beam",
            "calculix.cross_check_cantilever_frequency",
            "calculix.cross_check_thermal_steady_state",
            "calculix.check_mesh_convergence",
            "calculix.compute_joint_loads",
        }
        assert set(server.tool_ids) == expected

    def test_adapter_id_and_version(self, server: CalculixServer) -> None:
        assert server.adapter_id == "calculix"
        assert server.version == "0.1.0"

    def test_custom_config_propagated(self) -> None:
        cfg = CalculixConfig(max_solve_time=120)
        s = CalculixServer(config=cfg)
        assert s.config.max_solve_time == 120

    @pytest.mark.parametrize(
        "tool_id", ["calculix.run_fea", "calculix.run_thermal", "calculix.validate_mesh"]
    )
    def test_mesh_file_schema_tells_the_model_work_product_id_is_not_accepted(
        self, server: CalculixServer, tool_id: str
    ) -> None:
        """FORGE-223: same gap as freecad.generate_mesh -- this adapter has
        no Twin access either, and a model that only has a work_product_id
        needs to be told to stage it first, not left guessing after a bare
        'mesh_file is required' rejection."""
        schema = server._tools[tool_id].manifest.input_schema
        description = schema["properties"]["mesh_file"]["description"]
        assert "work_product_id" in description
        assert "twin.stage_work_product_file" in description


# ---------------------------------------------------------------------------
# TestRunFea
# ---------------------------------------------------------------------------


class TestRunFea:
    async def test_run_fea_success(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.run_fea(
            {
                "mesh_file": "/models/bracket.inp",
                "load_case": "gravity_1g",
                "analysis_type": "static_stress",
                "material": {"name": "steel"},
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": [0.0, 0.0, -100.0],
            }
        )
        assert result["max_von_mises"]["bracket_body"] == 145.2
        assert result["solver_time"] == 12.5
        assert result["mesh_elements"] == 45000

    async def test_run_fea_modal_analysis(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.run_fea(
            {
                "mesh_file": "/models/bracket.inp",
                "load_case": "vibration",
                "analysis_type": "modal",
                "material": {"name": "steel"},
                "fixed_node_set": "Surface1",
            }
        )
        assert "max_von_mises" in result

    async def test_run_fea_missing_mesh_file_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="mesh_file is required"):
            await server_with_mocks.run_fea(
                {"mesh_file": "", "load_case": "lc1", "analysis_type": "static_stress"}
            )

    async def test_run_fea_missing_load_case_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="load_case is required"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "",
                    "analysis_type": "static_stress",
                }
            )

    async def test_run_fea_invalid_analysis_type_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported analysis type"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "lc1",
                    "analysis_type": "buckling",
                }
            )


# ---------------------------------------------------------------------------
# TestRunFeaStaticStressValidation (FORGE-234)
# ---------------------------------------------------------------------------


_BASE_STATIC_STRESS_ARGS: dict[str, Any] = {
    "mesh_file": "/models/bracket.inp",
    "load_case": "gravity_1g",
    "analysis_type": "static_stress",
    "material": {"name": "steel"},
    "fixed_node_set": "Surface1",
    "load_node_set": "Surface2",
    "load_force_n": [0.0, 0.0, -100.0],
}


class TestRunFeaStaticStressValidation:
    """'static_stress' has no meaningful default material/boundary-condition/
    load -- run_fea must reject an incomplete request with a clear error
    instead of building a nonsense (or silently-wrong-unit) deck."""

    @pytest.mark.parametrize(
        "missing_field", ["material", "fixed_node_set", "load_node_set", "load_force_n"]
    )
    async def test_missing_required_field_raises_naming_it(
        self, server_with_mocks: CalculixServer, missing_field: str
    ) -> None:
        args = {**_BASE_STATIC_STRESS_ARGS, missing_field: None}
        with pytest.raises(ValueError, match=missing_field):
            await server_with_mocks.run_fea(args)

    async def test_material_must_be_an_object(self, server_with_mocks: CalculixServer) -> None:
        args = {**_BASE_STATIC_STRESS_ARGS, "material": "steel"}
        with pytest.raises(ValueError, match="'material' must be an object"):
            await server_with_mocks.run_fea(args)

    async def test_unknown_material_name_raises(self, server_with_mocks: CalculixServer) -> None:
        args = {**_BASE_STATIC_STRESS_ARGS, "material": {"name": "unobtainium"}}
        with pytest.raises(ValueError, match="unobtainium"):
            await server_with_mocks.run_fea(args)

    async def test_load_force_n_must_have_three_components(
        self, server_with_mocks: CalculixServer
    ) -> None:
        args = {**_BASE_STATIC_STRESS_ARGS, "load_force_n": [0.0, -100.0]}
        with pytest.raises(ValueError, match="load_force_n"):
            await server_with_mocks.run_fea(args)

    async def test_valid_request_passes_a_deck_spec_to_execute_solver(
        self, server_with_mocks: CalculixServer
    ) -> None:
        await server_with_mocks.run_fea(dict(_BASE_STATIC_STRESS_ARGS))
        call_args = server_with_mocks._execute_solver.call_args  # type: ignore[attr-defined]
        mesh_file, analysis_type, deck_spec = call_args[0]
        assert mesh_file == "/models/bracket.inp"
        assert analysis_type == "static_stress"
        assert deck_spec == {
            "youngs_modulus_mpa": pytest.approx(200000.0),
            "poissons_ratio": pytest.approx(0.30),
            "fixed_node_set": "Surface1",
            "load_node_set": "Surface2",
            "load_force_n": (0.0, 0.0, -100.0),
        }

    async def test_modal_analysis_missing_material_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="material"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "vibration",
                    "analysis_type": "modal",
                    "fixed_node_set": "Surface1",
                }
            )

    async def test_modal_analysis_missing_fixed_node_set_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="fixed_node_set"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "vibration",
                    "analysis_type": "modal",
                    "material": {"name": "steel"},
                }
            )

    async def test_modal_analysis_explicit_properties_without_name_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        """Unlike static_stress, 'modal' needs a real density -- and this
        codebase only ever looks density up by material name, so a caller
        who gave explicit E/poisson but no name still can't proceed."""
        with pytest.raises(ValueError, match="material.name is required"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "vibration",
                    "analysis_type": "modal",
                    "material": {"youngs_modulus_mpa": 200000.0, "poissons_ratio": 0.3},
                    "fixed_node_set": "Surface1",
                }
            )

    async def test_modal_analysis_passes_a_deck_spec_to_execute_solver(
        self, server_with_mocks: CalculixServer
    ) -> None:
        await server_with_mocks.run_fea(
            {
                "mesh_file": "/models/bracket.inp",
                "load_case": "vibration",
                "analysis_type": "modal",
                "material": {"name": "steel"},
                "fixed_node_set": "Surface1",
                "num_modes": 5,
            }
        )
        call_args = server_with_mocks._execute_solver.call_args  # type: ignore[attr-defined]
        mesh_file, analysis_type, deck_spec = call_args[0]
        assert mesh_file == "/models/bracket.inp"
        assert analysis_type == "modal"
        assert deck_spec == {
            "youngs_modulus_mpa": pytest.approx(200000.0),
            "poissons_ratio": pytest.approx(0.30),
            "density_tonne_mm3": pytest.approx(7850 * 1e-12),
            "fixed_node_set": "Surface1",
            "num_modes": 5,
        }

    async def test_modal_analysis_defaults_num_modes_to_three(
        self, server_with_mocks: CalculixServer
    ) -> None:
        await server_with_mocks.run_fea(
            {
                "mesh_file": "/models/bracket.inp",
                "load_case": "vibration",
                "analysis_type": "modal",
                "material": {"name": "steel"},
                "fixed_node_set": "Surface1",
            }
        )
        call_args = server_with_mocks._execute_solver.call_args  # type: ignore[attr-defined]
        assert call_args[0][2]["num_modes"] == 3


# ---------------------------------------------------------------------------
# TestRunThermal
# ---------------------------------------------------------------------------


class TestRunThermal:
    _VALID_ARGS: dict[str, Any] = {
        "mesh_file": "/models/heatsink.inp",
        "material": {"name": "aluminum"},
        "heat_source_node_set": "Surface2",
        "power_dissipation_w": 5.0,
        "sink_node_set": "Surface1",
        "sink_temp_c": 20.0,
    }

    async def test_run_thermal_success(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.run_thermal(self._VALID_ARGS)
        assert result["max_temperature_c"] == 85.3
        assert result["min_temperature_c"] == 22.1
        assert result["solver_time"] == 8.2

    async def test_run_thermal_missing_mesh_file_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="mesh_file is required"):
            await server_with_mocks.run_thermal({**self._VALID_ARGS, "mesh_file": ""})

    async def test_run_thermal_missing_material_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="material"):
            args = dict(self._VALID_ARGS)
            del args["material"]
            await server_with_mocks.run_thermal(args)

    async def test_run_thermal_missing_heat_source_node_set_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="heat_source_node_set"):
            args = dict(self._VALID_ARGS)
            del args["heat_source_node_set"]
            await server_with_mocks.run_thermal(args)

    async def test_run_thermal_missing_power_dissipation_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="power_dissipation_w"):
            args = dict(self._VALID_ARGS)
            del args["power_dissipation_w"]
            await server_with_mocks.run_thermal(args)

    async def test_run_thermal_missing_sink_node_set_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="sink_node_set"):
            args = dict(self._VALID_ARGS)
            del args["sink_node_set"]
            await server_with_mocks.run_thermal(args)

    async def test_run_thermal_missing_sink_temp_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="sink_temp_c"):
            args = dict(self._VALID_ARGS)
            del args["sink_temp_c"]
            await server_with_mocks.run_thermal(args)

    async def test_run_thermal_material_without_name_or_explicit_conductivity_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="provide either"):
            await server_with_mocks.run_thermal({**self._VALID_ARGS, "material": {}})

    async def test_run_thermal_unrecognized_material_name_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="unknown material"):
            await server_with_mocks.run_thermal(
                {**self._VALID_ARGS, "material": {"name": "unobtainium"}}
            )

    async def test_run_thermal_explicit_conductivity_bypasses_name_lookup(
        self, server_with_mocks: CalculixServer
    ) -> None:
        result = await server_with_mocks.run_thermal(
            {**self._VALID_ARGS, "material": {"thermal_conductivity_w_mk": 42.0}}
        )
        assert result["max_temperature_c"] == 85.3

    async def test_run_thermal_transient_mode_raises_not_implemented(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="not implemented"):
            await server_with_mocks.run_thermal({**self._VALID_ARGS, "analysis_mode": "transient"})

    async def test_run_thermal_passes_a_deck_spec_to_execute_thermal_solver(
        self, server_with_mocks: CalculixServer
    ) -> None:
        await server_with_mocks.run_thermal(self._VALID_ARGS)
        assert server_with_mocks._execute_thermal_solver.await_args is not None
        call_args = server_with_mocks._execute_thermal_solver.await_args.args
        assert call_args[0] == "/models/heatsink.inp"
        deck_spec = call_args[1]
        assert deck_spec["heat_source_node_set"] == "Surface2"
        assert deck_spec["sink_node_set"] == "Surface1"
        assert deck_spec["sink_temp_c"] == 20.0
        assert deck_spec["power_dissipation_w"] == 5.0
        # aluminum = 235.0 W/(m*K) -> 0.235 W/(mm*K)
        assert deck_spec["conductivity_w_mm_k"] == pytest.approx(0.235)


# ---------------------------------------------------------------------------
# TestValidateMesh
# ---------------------------------------------------------------------------


class TestValidateMesh:
    async def test_validate_mesh_success(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.validate_mesh(
            {"mesh_file": "/models/bracket.inp", "max_aspect_ratio": 10.0}
        )
        assert result["valid"] is True
        assert result["element_count"] == 45000
        assert result["node_count"] == 12000
        assert result["max_aspect_ratio"] == 3.2
        assert result["issues"] == []

    async def test_validate_mesh_missing_file_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="mesh_file is required"):
            await server_with_mocks.validate_mesh({"mesh_file": ""})

    async def test_validate_mesh_default_aspect_ratio(
        self, server_with_mocks: CalculixServer
    ) -> None:
        """validate_mesh uses default max_aspect_ratio of 10.0 when not provided."""
        result = await server_with_mocks.validate_mesh({"mesh_file": "/models/bracket.inp"})
        assert result["valid"] is True
        # Verify _validate_mesh_file was called with default value
        call_args = server_with_mocks._validate_mesh_file.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == 10.0


# ---------------------------------------------------------------------------
# TestCrossCheckCantileverBeam / TestCheckMeshConvergence (FORGE-280)
# ---------------------------------------------------------------------------


class TestCrossCheckCantileverBeam:
    """Thin handler over accuracy.cross_check_cantilever_bending — no
    _execute_* mock needed, this is pure computation, no solver I/O."""

    async def test_success(self, server: CalculixServer) -> None:
        result = await server.handle_cross_check_cantilever_beam(
            {
                "length_mm": 100,
                "width_mm": 10,
                "height_mm": 20,
                "force_n": 500,
                "fea_max_stress_mpa": 76.0,
            }
        )
        assert result["hand_calc_stress_mpa"] == pytest.approx(75.0, abs=0.1)
        assert result["within_tolerance"] is True

    async def test_default_tolerance_is_twenty_percent(self, server: CalculixServer) -> None:
        result = await server.handle_cross_check_cantilever_beam(
            {
                "length_mm": 100,
                "width_mm": 10,
                "height_mm": 20,
                "force_n": 500,
                "fea_max_stress_mpa": 76.0,
            }
        )
        assert result["tolerance_pct"] == 20.0

    @pytest.mark.parametrize(
        "missing", ["length_mm", "width_mm", "height_mm", "force_n", "fea_max_stress_mpa"]
    )
    async def test_missing_required_field_raises(
        self, server: CalculixServer, missing: str
    ) -> None:
        args = {
            "length_mm": 100,
            "width_mm": 10,
            "height_mm": 20,
            "force_n": 500,
            "fea_max_stress_mpa": 76.0,
        }
        del args[missing]
        with pytest.raises(ValueError, match="Missing required field"):
            await server.handle_cross_check_cantilever_beam(args)

    async def test_invalid_dimensions_raise(self, server: CalculixServer) -> None:
        with pytest.raises(ValueError, match="must all be positive"):
            await server.handle_cross_check_cantilever_beam(
                {
                    "length_mm": 0,
                    "width_mm": 10,
                    "height_mm": 20,
                    "force_n": 500,
                    "fea_max_stress_mpa": 76.0,
                }
            )


class TestCheckMeshConvergence:
    """Thin handler over accuracy.check_mesh_convergence."""

    async def test_success(self, server: CalculixServer) -> None:
        result = await server.handle_check_mesh_convergence(
            {
                "points": [
                    {"element_size_mm": 4.0, "max_von_mises_mpa": 100.0},
                    {"element_size_mm": 1.0, "max_von_mises_mpa": 101.0},
                ],
            }
        )
        assert result["converged"] is True

    async def test_missing_points_raises(self, server: CalculixServer) -> None:
        with pytest.raises(ValueError, match="points is required"):
            await server.handle_check_mesh_convergence({})

    async def test_single_point_raises(self, server: CalculixServer) -> None:
        with pytest.raises(ValueError, match="at least 2 points"):
            await server.handle_check_mesh_convergence(
                {"points": [{"element_size_mm": 1.0, "max_von_mises_mpa": 100.0}]}
            )


# ---------------------------------------------------------------------------
# TestUnmockedSolverRaisesNotImplemented
# ---------------------------------------------------------------------------


class TestExecuteThermalSolverParsesRealResults:
    """MET-661 follow-up: _execute_thermal_solver previously discarded the
    real solver output and always returned hardcoded max/min_temperature=0.0.
    FORGE-282: _execute_thermal_solver now also builds a real, solvable deck
    around the mesh-only .inp file first (mirroring _execute_solver's own
    FORGE-234 deck-building), so these tests provide a real mesh file, not
    just a bare path string."""

    _MESH_INP = """\
*Heading
 /workspace/box.inp
*NODE
1, 0, 0, 0
2, 0, 0, 10
3, 0, 20, 0
4, 0, 20, 10
5, 100, 0, 0
6, 100, 0, 10
7, 100, 20, 0
8, 100, 20, 10
9, 50, 10, 5
10, 50, 10, 6
*ELEMENT, type=CPS3, ELSET=Surface1
201, 1, 2, 3
202, 2, 4, 3
*ELEMENT, type=CPS3, ELSET=Surface2
203, 5, 6, 7
204, 6, 8, 7
*ELEMENT, type=C3D4, ELSET=Volume1
301, 1, 2, 3, 9
302, 5, 6, 7, 9
303, 3, 4, 9, 10
304, 6, 8, 9, 10
"""

    async def test_returns_real_parsed_temperatures_not_hardcoded_zeros(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mesh_path = tmp_path / "box.inp"
        mesh_path.write_text(self._MESH_INP, encoding="utf-8")
        frd_path = tmp_path / "box_solved.frd"
        frd_path.write_text(REAL_CCX_THERMAL_FRD, encoding="utf-8")

        captured_kwargs: dict[str, Any] = {}

        async def _fake_solver_run_fea(**kwargs: Any) -> dict[str, Any]:
            captured_kwargs.update(kwargs)
            return {"solver_time_s": 0.01, "result_files": [str(frd_path)]}

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        result = await server._execute_thermal_solver(
            str(mesh_path),
            {
                "conductivity_w_mm_k": 0.235,
                "heat_source_node_set": "Surface2",
                "power_dissipation_w": 5.0,
                "sink_node_set": "Surface1",
                "sink_temp_c": 20.0,
            },
        )

        solved_path = tmp_path / "box_solved.inp"
        assert solved_path.exists(), "the built deck must be written to <stem>_solved.inp"
        solved_text = solved_path.read_text(encoding="utf-8")
        assert "*CONDUCTIVITY" in solved_text
        assert "*CFLUX" in solved_text
        assert "TYPE=CPS3" not in solved_text  # surface elements dropped, see deck_builder

        assert captured_kwargs["mesh_file"] == str(solved_path)
        assert captured_kwargs["mesh_file"] != str(mesh_path)

        assert result["max_temperature_c"] == pytest.approx(100.0)
        assert result["min_temperature_c"] == pytest.approx(20.0)
        # FORGE-239: same frd_path surfacing as _execute_solver.
        assert result["frd_path"] == str(frd_path)


class TestEmptyResultIsNeverReportedAsSuccess:
    """FORGE-232: CalculiX solving a mesh-only deck (no *STEP/*STATIC/
    *BOUNDARY/*CLOAD, or no *NODE FILE/*EL FILE output request) exits 0 in a
    fraction of a second and produces either no .frd at all, or one with no
    result data for any node. Both used to come back as a normal, successful
    (if hollow) result -- every caller reported success on a stress finding
    that was never actually computed."""

    async def test_run_fea_raises_when_solver_produces_no_frd_at_all(
        self, server: CalculixServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_solver_run_fea(**_kwargs: Any) -> dict[str, Any]:
            return {"solver_time_s": 0.11, "result_files": []}  # no .frd produced

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        with pytest.raises(SolverError, match="no .frd result file"):
            await server._execute_solver("/models/test.inp", "static_stress")

    async def test_run_fea_raises_when_frd_has_no_node_data(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_frd = tmp_path / "empty.frd"
        empty_frd.write_text(" 9999\n", encoding="utf-8")  # real ccx "empty" shape

        async def _fake_solver_run_fea(**_kwargs: Any) -> dict[str, Any]:
            return {"solver_time_s": 0.11, "result_files": [str(empty_frd)]}

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        with pytest.raises(FrdParseError, match="node_count == 0"):
            await server._execute_solver("/models/test.inp", "static_stress")

    _THERMAL_MESH_INP = """\
*Heading
 /workspace/box.inp
*NODE
1, 0, 0, 0
2, 0, 0, 10
3, 0, 20, 0
4, 0, 20, 10
5, 100, 0, 0
6, 100, 0, 10
7, 100, 20, 0
8, 100, 20, 10
9, 50, 10, 5
10, 50, 10, 6
*ELEMENT, type=CPS3, ELSET=Surface1
201, 1, 2, 3
202, 2, 4, 3
*ELEMENT, type=CPS3, ELSET=Surface2
203, 5, 6, 7
204, 6, 8, 7
*ELEMENT, type=C3D4, ELSET=Volume1
301, 1, 2, 3, 9
302, 5, 6, 7, 9
303, 3, 4, 9, 10
304, 6, 8, 9, 10
"""

    async def test_run_thermal_raises_when_solver_produces_no_frd_at_all(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mesh_path = tmp_path / "box.inp"
        mesh_path.write_text(self._THERMAL_MESH_INP, encoding="utf-8")

        async def _fake_solver_run_fea(**_kwargs: Any) -> dict[str, Any]:
            return {"solver_time_s": 0.11, "result_files": []}

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        with pytest.raises(SolverError, match="no .frd result file"):
            await server._execute_thermal_solver(
                str(mesh_path),
                {
                    "conductivity_w_mm_k": 0.235,
                    "heat_source_node_set": "Surface2",
                    "power_dissipation_w": 5.0,
                    "sink_node_set": "Surface1",
                    "sink_temp_c": 20.0,
                },
            )


class TestExecuteSolverBuildsDeckForStaticStress:
    """FORGE-234: when a deck_spec is given, _execute_solver must build a
    real, solvable deck around the mesh-only .inp file and solve THAT
    (`<stem>_solved.inp`), not the original mesh_file -- the original has no
    *MATERIAL/*STEP/*BOUNDARY/*CLOAD/*NODE FILE/*EL FILE cards at all."""

    _MESH_INP = """\
*Heading
 /workspace/box.inp
*NODE
1, 0, 0, 0
2, 0, 0, 10
3, 0, 20, 0
4, 0, 20, 10
5, 100, 0, 0
6, 100, 0, 10
7, 100, 20, 0
8, 100, 20, 10
9, 50, 10, 5
10, 50, 10, 6
*ELEMENT, type=CPS3, ELSET=Surface1
201, 1, 2, 3
202, 2, 4, 3
*ELEMENT, type=CPS3, ELSET=Surface2
203, 5, 6, 7
204, 6, 8, 7
*ELEMENT, type=C3D4, ELSET=Volume1
301, 1, 2, 3, 9
302, 5, 6, 7, 9
303, 3, 4, 9, 10
304, 6, 8, 9, 10
"""

    async def test_builds_and_solves_a_deck_file_not_the_original_mesh(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mesh_path = tmp_path / "box.inp"
        mesh_path.write_text(self._MESH_INP, encoding="utf-8")
        frd_path = tmp_path / "box_solved.frd"
        frd_path.write_text(REAL_CCX_FRD, encoding="utf-8")

        captured_kwargs: dict[str, Any] = {}

        async def _fake_solver_run_fea(**kwargs: Any) -> dict[str, Any]:
            captured_kwargs.update(kwargs)
            return {"solver_time_s": 1.23, "result_files": [str(frd_path)]}

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        result = await server._execute_solver(
            str(mesh_path),
            "static_stress",
            {
                "youngs_modulus_mpa": 200000.0,
                "poissons_ratio": 0.30,
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": (0.0, 0.0, -100.0),
            },
        )

        solved_path = tmp_path / "box_solved.inp"
        assert solved_path.exists(), "the built deck must be written to <stem>_solved.inp"
        solved_text = solved_path.read_text(encoding="utf-8")
        assert "*MATERIAL, NAME=MAT1" in solved_text
        assert "*CLOAD" in solved_text
        assert "TYPE=CPS3" not in solved_text  # surface elements dropped, see deck_builder

        # solver_run_fea must have been called with the SOLVED file's path,
        # never the original mesh-only mesh_file.
        assert captured_kwargs["mesh_file"] == str(solved_path)
        assert captured_kwargs["mesh_file"] != str(mesh_path)

        assert result["solver_time"] == 1.23
        assert result["mesh_elements"] == 8  # node_count from REAL_CCX_FRD
        # FORGE-239: reported live -- run_fea writes "<stem>_solved.frd", the
        # model called extract_results on "<stem>.frd" and got a not-found.
        # The exact path must be surfaced directly, not left buried inside
        # result_files.
        assert result["frd_path"] == str(frd_path)
        # Surface1 (nodes 1-4, all x=0) is a real, legitimately small face
        # of this 100mm-long part -- no sanity-check warning.
        assert "warnings" not in result

    async def test_a_fixed_node_set_spanning_the_whole_part_warns(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-239's core regression: the reported bug's exact shape --
        a fixed_node_set that (wrongly) spans the part's full length along
        one axis instead of just one end -- must be flagged, not silently
        solved and returned as if it were a normal, trustworthy result."""
        mesh_path = tmp_path / "box.inp"
        mesh_path.write_text(self._MESH_INP, encoding="utf-8")
        frd_path = tmp_path / "box_solved.frd"
        frd_path.write_text(REAL_CCX_FRD, encoding="utf-8")

        async def _fake_solver_run_fea(**kwargs: Any) -> dict[str, Any]:
            return {"solver_time_s": 1.23, "result_files": [str(frd_path)]}

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        # Volume1 (all 10 nodes, x spans 0..100 -- the WHOLE part) is exactly
        # the reported bug's shape: a face-shaped name expected, but the
        # group actually claimed by "fixed_node_set" spans the full length.
        result = await server._execute_solver(
            str(mesh_path),
            "static_stress",
            {
                "youngs_modulus_mpa": 200000.0,
                "poissons_ratio": 0.30,
                "fixed_node_set": "Volume1",
                "load_node_set": "Surface2",
                "load_force_n": (0.0, 0.0, -100.0),
            },
        )

        assert "warnings" in result
        assert len(result["warnings"]) == 1
        assert "Volume1" in result["warnings"][0]
        assert "axis, x:" in result["warnings"][0]
        assert "100%" in result["warnings"][0]


class TestFixedNodeSetSpanWarning:
    """FORGE-239: the sanity-check helper in isolation."""

    _MESH_INP = TestExecuteSolverBuildsDeckForStaticStress._MESH_INP

    def _mesh(self, tmp_path: Path):
        from tool_registry.tools.calculix.inp_mesh import parse_mesh_inp

        mesh_path = tmp_path / "box.inp"
        mesh_path.write_text(self._MESH_INP, encoding="utf-8")
        return parse_mesh_inp(str(mesh_path))

    def test_a_real_small_face_does_not_warn(self, tmp_path: Path) -> None:
        from tool_registry.tools.calculix.adapter import _fixed_node_set_span_warning

        mesh = self._mesh(tmp_path)
        assert _fixed_node_set_span_warning(mesh, "Surface1") is None

    def test_a_group_spanning_the_full_part_warns_naming_the_axis(self, tmp_path: Path) -> None:
        from tool_registry.tools.calculix.adapter import _fixed_node_set_span_warning

        mesh = self._mesh(tmp_path)
        warning = _fixed_node_set_span_warning(mesh, "Volume1")
        assert warning is not None
        assert "Volume1" in warning
        assert "axis, x:" in warning

    def test_an_unknown_elset_name_returns_none_not_an_exception(self, tmp_path: Path) -> None:
        """Let run_fea's own downstream error paths report an unknown
        fixed_node_set -- this sanity check must never be what surfaces
        that error, or replace it with a confusing one."""
        from tool_registry.tools.calculix.adapter import _fixed_node_set_span_warning

        mesh = self._mesh(tmp_path)
        assert _fixed_node_set_span_warning(mesh, "NoSuchSet") is None


class TestUnmockedSolverRaisesOnMissingFiles:
    """Verify that calling solver methods without mocks raises on missing files."""

    async def test_execute_solver_raises(self, server: CalculixServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server._execute_solver("/models/test.inp", "static_stress")

    async def test_execute_thermal_solver_raises(self, server: CalculixServer) -> None:
        with pytest.raises((FileNotFoundError, NotImplementedError)):
            await server._execute_thermal_solver(
                "/models/test.inp",
                {
                    "conductivity_w_mm_k": 0.235,
                    "heat_source_node_set": "Surface2",
                    "power_dissipation_w": 5.0,
                    "sink_node_set": "Surface1",
                    "sink_temp_c": 20.0,
                },
            )

    async def test_validate_mesh_file_raises(self, server: CalculixServer) -> None:
        with pytest.raises((FileNotFoundError, NotImplementedError)):
            await server._validate_mesh_file("/models/test.inp", 10.0)


# ---------------------------------------------------------------------------
# TestJsonRpcIntegration
# ---------------------------------------------------------------------------


def _make_jsonrpc(
    method: str,
    params: dict[str, Any] | None = None,
    request_id: str = "1",
) -> str:
    """Helper to build a JSON-RPC 2.0 request string."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    return json.dumps(msg)


class TestJsonRpcIntegration:
    async def test_tool_list_via_handle_request(self, server: CalculixServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert len(response["result"]["tools"]) == 9

    async def test_tool_list_contains_expected_ids(self, server: CalculixServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        tool_ids = {t["tool_id"] for t in response["result"]["tools"]}
        assert tool_ids == {
            "calculix.run_fea",
            "calculix.run_thermal",
            "calculix.validate_mesh",
            "calculix.extract_results",
            "calculix.cross_check_cantilever_beam",
            "calculix.cross_check_cantilever_frequency",
            "calculix.cross_check_thermal_steady_state",
            "calculix.check_mesh_convergence",
            "calculix.compute_joint_loads",
        }

    async def test_tool_call_fea_via_handle_request(
        self, server_with_mocks: CalculixServer
    ) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.run_fea",
                "arguments": {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "gravity_1g",
                    "analysis_type": "static_stress",
                    "material": {"name": "steel"},
                    "fixed_node_set": "Surface1",
                    "load_node_set": "Surface2",
                    "load_force_n": [0.0, 0.0, -100.0],
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert response["result"]["status"] == "success"
        assert response["result"]["tool_id"] == "calculix.run_fea"
        data = response["result"]["data"]
        assert data["max_von_mises"]["bracket_body"] == 145.2
        assert "duration_ms" in response["result"]

    async def test_tool_call_thermal_via_handle_request(
        self, server_with_mocks: CalculixServer
    ) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.run_thermal",
                "arguments": {
                    "mesh_file": "/models/heatsink.inp",
                    "material": {"name": "aluminum"},
                    "heat_source_node_set": "Surface2",
                    "power_dissipation_w": 5.0,
                    "sink_node_set": "Surface1",
                    "sink_temp_c": 20.0,
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["max_temperature_c"] == 85.3

    async def test_tool_call_validate_mesh_via_handle_request(
        self, server_with_mocks: CalculixServer
    ) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.validate_mesh",
                "arguments": {"mesh_file": "/models/bracket.inp"},
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["valid"] is True

    async def test_health_check_via_handle_request(self, server: CalculixServer) -> None:
        request = _make_jsonrpc("health/check")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["adapter_id"] == "calculix"
        assert response["result"]["status"] == "healthy"
        assert response["result"]["version"] == "0.1.0"
        assert response["result"]["tools_available"] == 9

    async def test_tool_call_unknown_tool(self, server: CalculixServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {"tool_id": "calculix.nonexistent", "arguments": {}},
        )
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "error" in response
        assert response["error"]["code"] == -32601
        assert response["error"]["data"]["tool_id"] == "calculix.nonexistent"

    async def test_tool_call_validation_error_returns_execution_error(
        self, server: CalculixServer
    ) -> None:
        """When a handler raises ValueError, it should be wrapped as a tool execution error."""
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.run_fea",
                "arguments": {
                    "mesh_file": "",
                    "load_case": "lc1",
                    "analysis_type": "static_stress",
                },
            },
        )
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "error" in response
        assert response["error"]["code"] == -32001
        assert response["error"]["data"]["error_type"] == "TOOL_EXECUTION_ERROR"
        assert response["error"]["data"]["tool_id"] == "calculix.run_fea"


# ---------------------------------------------------------------------------
# TestHandleComputeJointLoads (FORGE-283)
# ---------------------------------------------------------------------------


class TestHandleComputeJointLoads:
    async def test_single_link_matches_hand_calc(self, server: CalculixServer) -> None:
        result = await server.handle_compute_joint_loads(
            {
                "links": [{"name": "link0", "com_world_mm": [100.0, 0.0, 0.0], "mass_kg": 2.0}],
                "joints": [{"name": "j0", "position_world_mm": [0.0, 0.0, 0.0]}],
            }
        )
        assert len(result["loads"]) == 1
        load = result["loads"][0]
        assert load["joint_name"] == "j0"
        assert load["supported_mass_kg"] == pytest.approx(2.0)
        assert load["reaction_force_n"][2] == pytest.approx(2.0 * 9.80665)
        assert result["worst_joint"]["joint_name"] == "j0"

    async def test_multi_link_worst_joint_is_base(self, server: CalculixServer) -> None:
        result = await server.handle_compute_joint_loads(
            {
                "links": [
                    {"name": "upper_arm", "com_world_mm": [100.0, 0.0, 0.0], "mass_kg": 1.5},
                    {"name": "forearm", "com_world_mm": [300.0, 0.0, 0.0], "mass_kg": 1.0},
                ],
                "joints": [
                    {"name": "base", "position_world_mm": [0.0, 0.0, 0.0]},
                    {"name": "elbow", "position_world_mm": [200.0, 0.0, 0.0]},
                ],
            }
        )
        assert result["worst_joint"]["joint_name"] == "base"

    async def test_with_payload(self, server: CalculixServer) -> None:
        result = await server.handle_compute_joint_loads(
            {
                "links": [{"name": "link0", "com_world_mm": [100.0, 0.0, 0.0], "mass_kg": 1.0}],
                "joints": [{"name": "j0", "position_world_mm": [0.0, 0.0, 0.0]}],
                "payload_mass_kg": 5.0,
                "payload_position_world_mm": [200.0, 0.0, 0.0],
            }
        )
        assert result["loads"][0]["supported_mass_kg"] == pytest.approx(6.0)

    async def test_missing_links_raises(self, server: CalculixServer) -> None:
        with pytest.raises(ValueError, match="links and joints"):
            await server.handle_compute_joint_loads({"joints": []})

    async def test_missing_joints_raises(self, server: CalculixServer) -> None:
        with pytest.raises(ValueError, match="links and joints"):
            await server.handle_compute_joint_loads(
                {"links": [{"name": "l0", "com_world_mm": [0.0, 0.0, 0.0], "mass_kg": 1.0}]}
            )

    async def test_payload_without_position_raises(self, server: CalculixServer) -> None:
        with pytest.raises(ValueError, match="payload_position_world_mm"):
            await server.handle_compute_joint_loads(
                {
                    "links": [{"name": "l0", "com_world_mm": [100.0, 0.0, 0.0], "mass_kg": 1.0}],
                    "joints": [{"name": "j0", "position_world_mm": [0.0, 0.0, 0.0]}],
                    "payload_mass_kg": 5.0,
                }
            )

    async def test_via_handle_request(self, server: CalculixServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.compute_joint_loads",
                "arguments": {
                    "links": [{"name": "l0", "com_world_mm": [100.0, 0.0, 0.0], "mass_kg": 2.0}],
                    "joints": [{"name": "j0", "position_world_mm": [0.0, 0.0, 0.0]}],
                },
            },
        )
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["worst_joint"]["joint_name"] == "j0"
