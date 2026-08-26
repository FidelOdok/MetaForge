"""End-to-end fidelity tests: generated decks solved by the real CalculiX binary.

These are the tests that establish the pipeline is *high fidelity* rather than
merely well-formed. Each one meshes a cantilever beam, generates a deck, runs
``ccx``, parses the result, and compares against a closed-form solution:

===============  ==========================================  ================
Analysis         Closed form                                 Tolerance
===============  ==========================================  ================
Static           Euler-Bernoulli tip deflection PL^3/(3EI)   2%
Static           Bending stress Mc/I                         10% (see below)
Modal            First bending mode of a cantilever          2%
Thermal          1D conduction dT = QL/(kA)                  5%
===============  ==========================================  ================

The stress tolerance is looser on purpose: beam theory assumes a St Venant
stress distribution and ignores the stress concentration at a fully built-in
root, so a correct FEA result is expected to sit slightly *above* Mc/I. A result
that matched Mc/I exactly would suggest the constraint was not applied.

Skipped when ``ccx`` is not installed.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import pytest

from tool_registry.tools.calculix.deck import DeckBuilder, StepOptions, parse_load_cases
from tool_registry.tools.calculix.materials import get_material
from tool_registry.tools.calculix.mesh import parse_inp_mesh
from tool_registry.tools.calculix.mesh_quality import evaluate_mesh
from tool_registry.tools.calculix.result_parser import parse_dat_frequencies, parse_frd_file

pytestmark = pytest.mark.skipif(
    shutil.which("ccx") is None,
    reason="CalculiX (ccx) is not installed",
)

# Cantilever beam geometry, in mm.
LENGTH = 100.0
WIDTH = 10.0
HEIGHT = 10.0

# Element counts along each axis. Four elements through the thickness with
# incompatible-mode hexes resolves the bending gradient without shear locking.
NX, NY, NZ = 40, 4, 4

MATERIAL = get_material("al6061_t6")

#: Second moment of area of a rectangular section, mm^4.
INERTIA = WIDTH * HEIGHT**3 / 12.0

#: First eigenvalue of a cantilever beam (Euler-Bernoulli), dimensionless.
BETA_L_1 = 1.875104

#: Thermal load: total power into the free end, in mW, and the held-end temperature.
HEAT_POWER_MW = 500.0
BASE_TEMPERATURE_K = 350.0


def build_beam_mesh(path: Path) -> Path:
    """Write a structured C3D8I hexahedral mesh of the beam."""
    node_id: dict[tuple[int, int, int], int] = {}
    lines = ["*NODE, NSET=Nall"]

    counter = 1
    for i in range(NX + 1):
        for j in range(NY + 1):
            for k in range(NZ + 1):
                node_id[(i, j, k)] = counter
                lines.append(f"{counter}, {i * LENGTH / NX}, {j * WIDTH / NY}, {k * HEIGHT / NZ}")
                counter += 1

    lines.append("*ELEMENT, TYPE=C3D8I, ELSET=Volume1")
    element = 1
    for i in range(NX):
        for j in range(NY):
            for k in range(NZ):
                connectivity = [
                    node_id[(i, j, k)],
                    node_id[(i + 1, j, k)],
                    node_id[(i + 1, j + 1, k)],
                    node_id[(i, j + 1, k)],
                    node_id[(i, j, k + 1)],
                    node_id[(i + 1, j, k + 1)],
                    node_id[(i + 1, j + 1, k + 1)],
                    node_id[(i, j + 1, k + 1)],
                ]
                lines.append(f"{element}, " + ", ".join(str(n) for n in connectivity))
                element += 1

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def solve(deck: Path) -> None:
    """Run ccx on a deck, failing the test with solver output on error."""
    result = subprocess.run(
        ["ccx", "-i", deck.stem],
        cwd=deck.parent,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"ccx failed with code {result.returncode}\n"
        f"stdout tail:\n{result.stdout[-2000:]}\n"
        f"stderr tail:\n{result.stderr[-1000:]}"
    )


@pytest.fixture(scope="module")
def beam(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A meshed cantilever beam, shared across the analyses in this module."""
    directory = tmp_path_factory.mktemp("calculix_fidelity")
    return build_beam_mesh(directory / "beam.inp")


class TestMeshQuality:
    def test_structured_hex_mesh_is_geometrically_perfect(self, beam: Path) -> None:
        """A regular grid of cubes should measure as flawless, and its measured
        volume should equal the beam's exact volume."""
        report = evaluate_mesh(parse_inp_mesh(beam))

        assert report.valid, report.issues
        assert report.max_aspect_ratio == pytest.approx(1.0)
        assert report.min_scaled_jacobian == pytest.approx(1.0)
        assert report.inverted_elements == []
        assert report.total_volume == pytest.approx(LENGTH * WIDTH * HEIGHT, rel=1e-9)


@pytest.fixture(scope="module")
def static_result(beam: Path) -> dict:
    """Solve the beam under a tip load and return the parsed results."""
    force = 100.0
    case = parse_load_cases(
        [
            {
                "name": "tip_load",
                "constraints": [{"region": {"face": "xmin"}, "kind": "fixed"}],
                "point_loads": [{"region": {"face": "xmax"}, "fz": -force}],
            }
        ]
    )[0]

    deck = DeckBuilder(parse_inp_mesh(beam), MATERIAL).write(
        case, beam.with_name("beam_static.inp"), StepOptions(analysis="static")
    )
    solve(deck)

    parsed = parse_frd_file(str(beam.with_name("beam_static.frd")))
    parsed["applied_force_n"] = force
    return parsed


@pytest.fixture(scope="module")
def modal_modes(beam: Path) -> list[dict[str, float]]:
    """Extract the beam's first four eigenfrequencies."""
    case = parse_load_cases(
        [
            {
                "name": "modal",
                "constraints": [{"region": {"face": "xmin"}, "kind": "fixed"}],
            }
        ]
    )[0]

    deck = DeckBuilder(parse_inp_mesh(beam), MATERIAL).write(
        case,
        beam.with_name("beam_modal.inp"),
        StepOptions(analysis="modal", eigenmodes=4),
    )
    solve(deck)
    return parse_dat_frequencies(str(beam.with_name("beam_modal.dat")))


@pytest.fixture(scope="module")
def thermal_result(beam: Path) -> dict:
    """Solve 1D conduction along the beam and return the parsed results."""
    case = parse_load_cases(
        [
            {
                "name": "conduction",
                "thermal_boundaries": [
                    {"region": {"face": "xmin"}, "temperature_k": BASE_TEMPERATURE_K}
                ],
                "heat_fluxes": [{"region": {"face": "xmax"}, "power_mw": HEAT_POWER_MW}],
            }
        ]
    )[0]

    deck = DeckBuilder(parse_inp_mesh(beam), MATERIAL).write(
        case, beam.with_name("beam_thermal.inp"), StepOptions(analysis="thermal")
    )
    solve(deck)
    return parse_frd_file(str(beam.with_name("beam_thermal.frd")))


class TestStaticFidelity:
    def test_tip_deflection_matches_beam_theory(self, static_result: dict) -> None:
        """delta = PL^3 / (3EI)."""
        force = static_result["applied_force_n"]
        expected = force * LENGTH**3 / (3.0 * MATERIAL.youngs_modulus_mpa * INERTIA)
        assert static_result["displacement"]["max"] == pytest.approx(expected, rel=0.02)

    def test_bending_stress_is_near_mc_over_i(self, static_result: dict) -> None:
        """sigma = Mc/I, with the FEA sitting slightly above it at the built-in root."""
        force = static_result["applied_force_n"]
        expected = (force * LENGTH) * (HEIGHT / 2.0) / INERTIA
        actual = static_result["stress"]["max"]

        assert actual == pytest.approx(expected, rel=0.10)
        assert actual >= expected, (
            "FEA stress below Mc/I suggests the root constraint was not applied"
        )

    def test_solve_produced_a_populated_result_file(self, static_result: dict) -> None:
        """The historical failure mode was a clean solve with an empty .frd."""
        assert static_result["stress"]["nodes"], "no nodal stress in the .frd"
        assert static_result["displacement"]["nodes"], "no nodal displacement in the .frd"

    def test_safety_factor_is_derived_from_real_yield_strength(self, static_result: dict) -> None:
        stress = static_result["stress"]["max"]
        expected = MATERIAL.yield_strength_mpa / stress
        assert MATERIAL.safety_factor(stress) == pytest.approx(expected)
        assert 1.0 < MATERIAL.safety_factor(stress) < 100.0


class TestModalFidelity:
    def test_first_bending_mode_matches_beam_theory(
        self, modal_modes: list[dict[str, float]]
    ) -> None:
        """f1 = (beta*L)^2 / (2*pi) * sqrt(EI / (rho*A*L^4))."""
        assert modal_modes, "modal solve produced no eigenfrequencies"

        mass_per_length = MATERIAL.density_tonne_mm3 * WIDTH * HEIGHT
        expected = (BETA_L_1**2 / (2.0 * math.pi)) * math.sqrt(
            MATERIAL.youngs_modulus_mpa * INERTIA / (mass_per_length * LENGTH**4)
        )
        assert modal_modes[0]["frequency_hz"] == pytest.approx(expected, rel=0.02)

    def test_square_section_yields_a_degenerate_mode_pair(
        self, modal_modes: list[dict[str, float]]
    ) -> None:
        """Bending about Y and Z is identical for a square section, so the first
        two modes must coincide -- a check that the mass matrix is real."""
        assert len(modal_modes) >= 2
        assert modal_modes[0]["frequency_hz"] == pytest.approx(
            modal_modes[1]["frequency_hz"], rel=1e-4
        )


class TestThermalFidelity:
    def test_conduction_matches_the_one_dimensional_solution(self, thermal_result: dict) -> None:
        """dT = QL / (kA) along a bar held at one end and heated at the other."""
        area = WIDTH * HEIGHT
        rise = HEAT_POWER_MW * LENGTH / (MATERIAL.thermal_conductivity * area)

        assert thermal_result["temperature"]["min"] == pytest.approx(BASE_TEMPERATURE_K, abs=0.01)
        assert thermal_result["temperature"]["max"] == pytest.approx(
            BASE_TEMPERATURE_K + rise, rel=0.05
        )

    def test_boundary_conditions_actually_reach_the_solver(self, thermal_result: dict) -> None:
        """Previously the payload was validated then discarded, so the solve
        carried no thermal load and every node came back at the initial value."""
        assert thermal_result["temperature"]["max"] > thermal_result["temperature"]["min"]
