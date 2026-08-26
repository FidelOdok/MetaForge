"""Tests for the CalculiX adapter's deck-generating analysis path.

Before this path existed the adapter accepted ``load_cases``, ``material`` and
thermal ``boundary_conditions`` and then dropped all three on the floor: the
solver ran against whatever the mesh file already contained, which for a
mesher's geometry-only output is no physics at all. ``safety_factor`` was never
present in any response, so every caller read it as 0.0.

These tests pin the wiring. The solver itself is stubbed here; the proof that
the generated decks actually solve correctly lives in
``tests/integration/test_calculix_solver_fidelity.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.calculix.adapter import CalculixServer
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.deck import DeckError

CUBE_MESH = """\
*NODE, NSET=Nall
1, 0.0, 0.0, 0.0
2, 1.0, 0.0, 0.0
3, 1.0, 1.0, 0.0
4, 0.0, 1.0, 0.0
5, 0.0, 0.0, 1.0
6, 1.0, 0.0, 1.0
7, 1.0, 1.0, 1.0
8, 0.0, 1.0, 1.0
*ELEMENT, TYPE=C3D8, ELSET=Volume1
1, 1, 2, 3, 4, 5, 6, 7, 8
"""

STATIC_CASE: dict[str, Any] = {
    "name": "tip_load",
    "constraints": [{"region": {"face": "zmin"}, "kind": "fixed"}],
    "point_loads": [{"region": {"face": "zmax"}, "fz": -100.0}],
}


@pytest.fixture()
def mesh_file(tmp_path: Path) -> Path:
    path = tmp_path / "cube.inp"
    path.write_text(CUBE_MESH, encoding="utf-8")
    return path


def stub_solver(server: CalculixServer, stress: float, displacement: float = 0.1) -> AsyncMock:
    """Replace the solver with one returning a fixed parsed result."""
    mock = AsyncMock(
        return_value={
            "max_von_mises": {"global": stress},
            "stress": {"max": stress},
            "displacement": {"max": displacement},
            "solver_time": 1.5,
            "mesh_elements": 1,
            "result_files": [],
        }
    )
    server._execute_solver = mock  # type: ignore[method-assign]
    return mock


@pytest.fixture()
def server(tmp_path: Path) -> CalculixServer:
    return CalculixServer(CalculixConfig(work_dir=str(tmp_path)))


class TestDeckGeneration:
    async def test_structured_load_case_generates_a_deck(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        """The solver must be handed a generated deck, not the bare mesh."""
        solver = stub_solver(server, stress=100.0)

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [STATIC_CASE],
            }
        )

        solved_path = Path(solver.call_args[0][0])
        assert solved_path != mesh_file
        assert solved_path.name == "cube_tip_load.inp"

        deck = solved_path.read_text(encoding="utf-8")
        assert "*STATIC" in deck
        assert "*BOUNDARY" in deck
        assert "*CLOAD" in deck
        assert result["deck_files"] == [str(solved_path)]

    async def test_material_reaches_the_generated_deck(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        """The material argument was previously accepted and discarded."""
        solver = stub_solver(server, stress=100.0)

        await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "material": "ti6al4v",
                "load_cases": [STATIC_CASE],
            }
        )

        deck = Path(solver.call_args[0][0]).read_text(encoding="utf-8")
        assert "*MATERIAL, NAME=Ti-6Al-4V" in deck
        assert "113800" in deck

    async def test_material_overrides_are_applied(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        result = await self._run_with_override(server, mesh_file, 300.0)
        assert result["yield_strength_mpa"] == 300.0

    @staticmethod
    async def _run_with_override(
        server: CalculixServer, mesh_file: Path, yield_mpa: float
    ) -> dict[str, Any]:
        stub_solver(server, stress=100.0)
        return await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "material": "steel_1018",
                "material_overrides": {"yield_strength_mpa": yield_mpa},
                "load_cases": [STATIC_CASE],
            }
        )

    async def test_nlgeom_flag_reaches_the_deck(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        solver = stub_solver(server, stress=100.0)
        await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "nlgeom": True,
                "load_cases": [STATIC_CASE],
            }
        )
        deck = Path(solver.call_args[0][0]).read_text(encoding="utf-8")
        assert "*STEP, NLGEOM" in deck


class TestDeckPlacement:
    """The solver runs ``ccx -i <job>`` from ``config.work_dir``, so a deck
    written anywhere else is invisible to it."""

    async def test_deck_is_written_into_the_configured_work_dir(
        self, mesh_file: Path, tmp_path: Path
    ) -> None:
        work_dir = tmp_path / "solver_scratch"
        server = CalculixServer(CalculixConfig(work_dir=str(work_dir)))
        solver = stub_solver(server, stress=100.0)

        await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [STATIC_CASE],
            }
        )

        deck_path = Path(solver.call_args[0][0])
        assert deck_path.parent == work_dir
        assert deck_path.exists()

    async def test_generated_deck_includes_the_mesh_by_absolute_path(
        self, mesh_file: Path, tmp_path: Path
    ) -> None:
        """The deck and the mesh no longer share a directory, so a bare
        filename in the *INCLUDE would not resolve."""
        server = CalculixServer(CalculixConfig(work_dir=str(tmp_path / "elsewhere")))
        solver = stub_solver(server, stress=100.0)

        await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [STATIC_CASE],
            }
        )

        deck = Path(solver.call_args[0][0]).read_text(encoding="utf-8")
        assert f"*INCLUDE, INPUT={mesh_file.resolve()}" in deck

    async def test_unusable_work_dir_falls_back_beside_the_mesh(
        self, mesh_file: Path, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("", encoding="utf-8")

        server = CalculixServer(CalculixConfig(work_dir=str(blocker)))
        solver = stub_solver(server, stress=100.0)

        await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [STATIC_CASE],
            }
        )

        assert Path(solver.call_args[0][0]).parent == mesh_file.parent

    async def test_similarly_named_cases_get_distinct_decks(
        self, mesh_file: Path, tmp_path: Path
    ) -> None:
        """Names that collide after slugification must not overwrite each other,
        or the second solve is reported for both."""
        server = CalculixServer(CalculixConfig(work_dir=str(tmp_path / "decks")))
        solver = AsyncMock(
            return_value={
                "stress": {"max": 10.0},
                "displacement": {"max": 0.1},
                "solver_time": 1.0,
                "result_files": [],
            }
        )
        server._execute_solver = solver  # type: ignore[method-assign]

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [
                    {**STATIC_CASE, "name": "hard landing"},
                    {**STATIC_CASE, "name": "hard/landing"},
                ],
            }
        )

        decks = result["deck_files"]
        assert len(set(decks)) == 2, decks


class TestSafetyFactor:
    async def test_safety_factor_is_yield_over_stress(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        """Al6061-T6 yields at 276 MPa, so 92 MPa is a safety factor of 3."""
        stub_solver(server, stress=92.0)

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "material": "al6061_t6",
                "load_cases": [STATIC_CASE],
            }
        )

        assert result["safety_factor"] == pytest.approx(3.0)
        assert result["yield_strength_mpa"] == 276.0
        assert result["material"] == "al6061_t6"

    async def test_safety_factor_is_present_on_the_legacy_path(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        """Callers read ``safety_factor`` unconditionally; it must never be absent."""
        stub_solver(server, stress=92.0)

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "gravity_1g",
                "analysis_type": "static_stress",
            }
        )

        assert "safety_factor" in result
        assert result["safety_factor"] == pytest.approx(3.0)

    async def test_zero_stress_reports_infinite_margin(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        stub_solver(server, stress=0.0)

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [STATIC_CASE],
            }
        )

        assert result["safety_factor"] == float("inf")


class TestMultipleLoadCases:
    async def test_each_case_is_solved_and_the_worst_governs(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        stresses = iter([50.0, 138.0])

        async def solve(deck_path: str, analysis_type: str) -> dict[str, Any]:
            stress = next(stresses)
            return {
                "stress": {"max": stress},
                "displacement": {"max": 0.1},
                "solver_time": 1.0,
                "result_files": [],
            }

        server._execute_solver = AsyncMock(side_effect=solve)  # type: ignore[method-assign]

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "material": "al6061_t6",
                "load_cases": [
                    {**STATIC_CASE, "name": "cruise"},
                    {**STATIC_CASE, "name": "hard_landing"},
                ],
            }
        )

        assert result["max_von_mises"] == {"cruise": 50.0, "hard_landing": 138.0}
        assert result["governing_load_case"] == "hard_landing"
        # The governing case sets the margin: 276 / 138 = 2.0.
        assert result["safety_factor"] == pytest.approx(2.0)
        assert len(result["load_cases"]) == 2

    async def test_per_case_results_are_reported(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        stub_solver(server, stress=92.0)

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [STATIC_CASE],
            }
        )

        case = result["load_cases"][0]
        assert case["name"] == "tip_load"
        assert case["max_von_mises_mpa"] == pytest.approx(92.0)
        assert case["safety_factor"] == pytest.approx(3.0)

    async def test_solver_time_is_summed_across_cases(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        stub_solver(server, stress=92.0)

        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "structural",
                "analysis_type": "static_stress",
                "load_cases": [
                    {**STATIC_CASE, "name": "a"},
                    {**STATIC_CASE, "name": "b"},
                ],
            }
        )

        assert result["solver_time"] == pytest.approx(3.0)


class TestThermalWiring:
    async def test_boundary_conditions_generate_a_heat_transfer_deck(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        """The payload was previously validated as non-empty and then dropped."""
        solver = AsyncMock(
            return_value={
                "max_temperature": 353.0,
                "min_temperature": 350.0,
                "temperature_distribution": {},
                "solver_time": 2.0,
                "result_files": [],
            }
        )
        server._execute_thermal_solver = solver  # type: ignore[method-assign]

        result = await server.run_thermal(
            {
                "mesh_file": str(mesh_file),
                "material": "al6061_t6",
                "boundary_conditions": {
                    "thermal_boundaries": [{"region": {"face": "zmin"}, "temperature_k": 350.0}],
                    "heat_fluxes": [{"region": {"face": "zmax"}, "power_mw": 500.0}],
                },
                "analysis_mode": "steady_state",
            }
        )

        deck = Path(solver.call_args[0][0]).read_text(encoding="utf-8")
        assert "*HEAT TRANSFER, STEADY STATE" in deck
        assert "*CFLUX" in deck
        assert "*CONDUCTIVITY" in deck
        assert result["deck_files"]

    async def test_transient_mode_is_honoured(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        solver = AsyncMock(return_value={"max_temperature": 353.0, "min_temperature": 350.0})
        server._execute_thermal_solver = solver  # type: ignore[method-assign]

        await server.run_thermal(
            {
                "mesh_file": str(mesh_file),
                "boundary_conditions": {
                    "thermal_boundaries": [{"region": {"face": "zmin"}, "temperature_k": 350.0}],
                },
                "analysis_mode": "transient",
            }
        )

        deck = Path(solver.call_args[0][0]).read_text(encoding="utf-8")
        assert "STEADY STATE" not in deck

    async def test_legacy_payload_falls_back_without_a_deck(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        """A payload naming no region cannot be turned into a deck; it must
        still solve the file as authored rather than failing."""
        solver = AsyncMock(return_value={"max_temperature": 0.0, "min_temperature": 0.0})
        server._execute_thermal_solver = solver  # type: ignore[method-assign]

        await server.run_thermal(
            {
                "mesh_file": str(mesh_file),
                "boundary_conditions": {"ambient_temp": 25.0, "heat_flux": 100.0},
            }
        )

        assert solver.call_args[0][0] == str(mesh_file)

    async def test_invalid_analysis_mode_is_rejected(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported analysis mode"):
            await server.run_thermal(
                {
                    "mesh_file": str(mesh_file),
                    "boundary_conditions": {"ambient_temp": 25.0},
                    "analysis_mode": "cyclic",
                }
            )


class TestMeshValidation:
    async def test_validate_mesh_measures_real_geometry(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        result = await server.validate_mesh({"mesh_file": str(mesh_file)})

        assert result["valid"] is True
        assert result["element_count"] == 1
        assert result["node_count"] == 8
        assert result["max_aspect_ratio"] == pytest.approx(1.0)
        assert result["min_scaled_jacobian"] == pytest.approx(1.0)
        assert result["total_volume_mm3"] == pytest.approx(1.0)

    async def test_multi_line_elements_are_counted_once(
        self, server: CalculixServer, tmp_path: Path
    ) -> None:
        """Line-counting reported a C3D20 as three elements; parsing reports one."""
        path = tmp_path / "quadratic.inp"
        nodes = "\n".join(f"{i}, {i * 0.1}, {i * 0.2}, {i * 0.3}" for i in range(1, 21))
        connectivity = ", ".join(str(i) for i in range(1, 21))
        path.write_text(
            f"*NODE\n{nodes}\n*ELEMENT, TYPE=C3D20, ELSET=Volume1\n1, {connectivity}\n",
            encoding="utf-8",
        )

        result = await server.validate_mesh({"mesh_file": str(path)})
        assert result["element_count"] == 1
        assert result["node_count"] == 20

    async def test_sliver_mesh_is_reported_invalid(
        self, server: CalculixServer, tmp_path: Path
    ) -> None:
        path = tmp_path / "sliver.inp"
        path.write_text(
            "*NODE\n"
            "1, 0.0, 0.0, 0.0\n"
            "2, 1.0, 0.0, 0.0\n"
            "3, 0.0, 1.0, 0.0\n"
            "4, 0.3, 0.3, 0.001\n"
            "*ELEMENT, TYPE=C3D4, ELSET=Volume1\n1, 1, 2, 3, 4\n",
            encoding="utf-8",
        )

        result = await server.validate_mesh({"mesh_file": str(path)})
        assert result["valid"] is False
        assert result["sliver_element_count"] == 1
        assert result["worst_elements"]

    async def test_missing_mesh_raises(self, server: CalculixServer, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await server.validate_mesh({"mesh_file": str(tmp_path / "absent.inp")})


class TestErrorReporting:
    async def test_bare_string_load_cases_are_rejected_with_guidance(
        self, server: CalculixServer, mesh_file: Path
    ) -> None:
        with pytest.raises(DeckError, match="constraints"):
            await server.run_fea(
                {
                    "mesh_file": str(mesh_file),
                    "load_case": "gravity_1g",
                    "analysis_type": "static_stress",
                    "load_cases": "gravity_1g",
                }
            )

    async def test_unparseable_mesh_names_the_file(
        self, server: CalculixServer, tmp_path: Path
    ) -> None:
        path = tmp_path / "empty.inp"
        path.write_text("** nothing here\n", encoding="utf-8")

        with pytest.raises(DeckError, match="empty.inp"):
            await server.run_fea(
                {
                    "mesh_file": str(path),
                    "load_case": "structural",
                    "analysis_type": "static_stress",
                    "load_cases": [STATIC_CASE],
                }
            )

    async def test_unknown_material_is_fatal(self, server: CalculixServer, mesh_file: Path) -> None:
        """Substituting a default would silently change the physics."""
        with pytest.raises(KeyError):
            await server.run_fea(
                {
                    "mesh_file": str(mesh_file),
                    "load_case": "structural",
                    "analysis_type": "static_stress",
                    "material": "unobtainium",
                    "load_cases": [STATIC_CASE],
                }
            )
