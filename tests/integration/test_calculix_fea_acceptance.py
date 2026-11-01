"""CalculiX FEA acceptance test against a REAL ccx solve (FORGE-232/234).

This is the permanent form of scenario FS-E5-02 (FORGE-232's own fix
direction #4): a real gmsh-meshed cantilever, solved through the actual
``calculix.run_fea`` deck-building path (``deck_builder.build_static_stress_
deck`` -> real ``ccx`` binary -> ``result_parser``), must match Euler-
Bernoulli beam theory within 10%.

Skipped automatically when the ``ccx`` binary isn't on PATH, so it's a no-op
in CI (which has no CalculiX solver installed -- see the freecad-adapter's
own ``test_freecad_authoring_vertical.py`` for the identical pattern used
there). To run it against the real solver, execute inside the
calculix-adapter container::

    docker exec -w /app <calculix-adapter-container> \
        python3 -m pytest tests/integration/test_calculix_fea_acceptance.py -v

The mesh fixture (``fixtures/cantilever_1.5mm.inp``) is a real, checked-in
gmsh 4.x output for a 100x20x10mm steel cantilever (STEP -> gmsh -clmax 1.5),
captured live on fidel-dev -- not hand-authored -- so this test exercises the
exact same mesh shape (mixed C3D4/CPS3/T3D2 elements, per-STEP-face ELSETs)
that ``freecad.generate_mesh`` actually produces. Fixed at Surface1 (x=0),
100N in -Z at Surface2 (x=100). Live-validated result at this exact mesh
resolution: ~7.2% error against beam theory -- comfortably inside the 10%
bar with real margin (a 2mm mesh measured ~12.3%, outside tolerance, which
is why 1.5mm was chosen over a coarser/smaller fixture).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tool_registry.tools.calculix.adapter import CalculixServer
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.inp_mesh import parse_mesh_inp

pytestmark = pytest.mark.skipif(
    shutil.which("ccx") is None,
    reason="ccx (CalculiX) binary unavailable -- run inside the calculix-adapter container",
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Beam geometry/material baked into the fixture mesh (see module docstring).
_LENGTH_MM = 100.0
_WIDTH_MM = 20.0
_HEIGHT_MM = 10.0
_YOUNGS_MODULUS_MPA = 200000.0  # steel
_LOAD_N = 100.0
_ACCEPTANCE_TOLERANCE = 0.10  # FORGE-232 scenario FS-E5-02


def _euler_bernoulli_tip_deflection_mm() -> float:
    """delta = P*L^3 / (3*E*I) for a cantilever end load."""
    moment_of_inertia = _WIDTH_MM * _HEIGHT_MM**3 / 12.0
    return _LOAD_N * _LENGTH_MM**3 / (3 * _YOUNGS_MODULUS_MPA * moment_of_inertia)


class TestFeaCantileverAcceptance:
    async def test_tip_deflection_matches_beam_theory_within_10_percent(
        self, tmp_path: Path
    ) -> None:
        mesh_file = tmp_path / "cantilever.inp"
        mesh_file.write_bytes((_FIXTURES_DIR / "cantilever_1.5mm.inp").read_bytes())

        server = CalculixServer(config=CalculixConfig(work_dir=str(tmp_path)))
        result = await server.run_fea(
            {
                "mesh_file": str(mesh_file),
                "load_case": "fs_e5_02_acceptance",
                "analysis_type": "static_stress",
                "material": {"name": "steel"},
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": [0.0, 0.0, -_LOAD_N],
            }
        )

        # Sanity band on max von Mises stress (analytical bending stress
        # sigma = M*c/I ~= 30 MPa here) -- catches a gross unit error (e.g.
        # MPa/Pa confusion, off by 1e6) without being strict about von Mises
        # vs. pure bending stress or exactly where the FEA max lands.
        assert 15.0 < result["max_von_mises"]["global"] < 45.0

        mesh = parse_mesh_inp(str(mesh_file))
        tip_nodes = mesh.node_ids_for_elset("Surface2")
        node_disp = result["displacement"]["nodes"]
        tip_values = [node_disp[n] for n in tip_nodes if n in node_disp]
        assert tip_values, "no displacement data for the loaded face"
        avg_tip_deflection = sum(tip_values) / len(tip_values)

        theory = _euler_bernoulli_tip_deflection_mm()
        relative_error = abs(avg_tip_deflection - theory) / theory
        assert relative_error < _ACCEPTANCE_TOLERANCE, (
            f"FEA tip deflection {avg_tip_deflection:.4f}mm vs beam theory "
            f"{theory:.4f}mm -- {relative_error * 100:.1f}% error exceeds the "
            f"ticket's {_ACCEPTANCE_TOLERANCE * 100:.0f}% FS-E5-02 acceptance bar"
        )
