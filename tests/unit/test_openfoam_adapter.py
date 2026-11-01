"""The OpenFOAM adapter (FORGE-557).

Three layers, none needing OpenFOAM:

* the case generator and the STL reader, as pure functions;
* the result parsers, run against files captured from a real OpenFOAM 11 run
  (``tests/unit/fixtures/openfoam``) rather than against text typed from memory;
* the adapter's orchestration, driven by a *fake* OpenFOAM, a ``bashrc`` that
  defines ``blockMesh``, ``snappyHexMesh`` and the rest as shell functions
  replaying that captured run. That exercises stage ordering, failure reporting,
  leak detection and timeouts.

The real solver is checked separately, live, against the Hagen-Poiseuille
pressure drop for a straight pipe; see ``docs/capability-matrix.md``.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from tests.unit.openfoam_geometry import pipe_triangles, write_pipe
from tool_registry.tools.openfoam import case_builder as cb
from tool_registry.tools.openfoam import results, stl
from tool_registry.tools.openfoam.adapter import OpenFoamServer
from tool_registry.tools.openfoam.config import OpenFoamConfig

FIXTURES = Path(__file__).parent / "fixtures" / "openfoam"


def request_args(**overrides: object) -> dict:
    args: dict = {
        "inlet_stl": "inlet.stl",
        "outlet_stl": "outlet.stl",
        "wall_stls": ["wall.stl"],
        "stl_units": "m",
        "inside_point": [0.2, 0.0, 0.0],
        "fluid": {"density_kg_m3": 1.2, "kinematic_viscosity_m2_s": 1e-5},
        "inlet_velocity_m_s": 0.025,
        "turbulence": "laminar",
    }
    args.update(overrides)
    return args


# ---------------------------------------------------------------------------
# STL
# ---------------------------------------------------------------------------


class TestStl:
    def test_ascii_round_trip_with_scale(self, tmp_path: Path) -> None:
        tris = [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]
        path = tmp_path / "a.stl"
        stl.write_ascii_stl(path, "a", tris, scale=0.001)
        back = stl.read_stl(path)
        assert back == [(0.0, 0.0, 0.0, 0.001, 0.0, 0.0, 0.0, 0.001, 0.0)]

    def test_binary_is_recognised_by_size_even_when_it_starts_with_solid(
        self, tmp_path: Path
    ) -> None:
        header = b"solid made by someone".ljust(80, b" ")
        body = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
        path = tmp_path / "b.stl"
        path.write_bytes(header + struct.pack("<I", 1) + body)
        assert stl.read_stl(path) == [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]

    def test_a_file_with_no_triangles_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.stl"
        path.write_text("solid x\nendsolid x\n")
        with pytest.raises(stl.StlError, match="no triangles"):
            stl.read_stl(path)

    def test_bounds(self) -> None:
        lo, hi = stl.bounds(pipe_triangles(0.01, 0.4, 16)["wall"])
        assert lo[0] == 0 and hi[0] == pytest.approx(0.4)
        assert hi[1] == pytest.approx(0.01, abs=1e-9)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestRequest:
    def test_fluid_properties_are_required(self) -> None:
        args = request_args()
        del args["fluid"]
        with pytest.raises(ValueError):
            cb.FlowRequest.model_validate(args)

    def test_units_are_required(self) -> None:
        args = request_args()
        del args["stl_units"]
        with pytest.raises(ValueError):
            cb.FlowRequest.model_validate(args)

    def test_turbulent_runs_need_the_inlet_values(self) -> None:
        with pytest.raises(ValueError, match="turbulence_intensity"):
            cb.FlowRequest.model_validate(request_args(turbulence="kOmegaSST"))

    def test_refinement_levels_must_be_ordered(self) -> None:
        with pytest.raises(ValueError):
            cb.FlowRequest.model_validate(
                request_args(
                    mesh={"surface_refinement_min": 3, "surface_refinement_max": 1},
                )
            )

    def test_a_surface_outside_the_workspace_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "ws").mkdir()
        outside = tmp_path / "secret.stl"
        outside.write_text("solid s\nendsolid s\n")
        with pytest.raises(ValueError, match="outside the adapter workspace"):
            cb.resolve_inside(tmp_path / "ws", "../secret.stl")
        with pytest.raises(ValueError, match="outside the adapter workspace"):
            cb.resolve_inside(tmp_path / "ws", str(outside))

    def test_a_missing_surface_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="surface not found"):
            cb.resolve_inside(tmp_path, "nope.stl")


# ---------------------------------------------------------------------------
# Case files
# ---------------------------------------------------------------------------


@pytest.fixture
def pipe(tmp_path: Path) -> Path:
    write_pipe(tmp_path, radius=0.01, length=0.4, segments=32)
    return tmp_path


def build(pipe_dir: Path, **overrides: object) -> dict:
    req = cb.FlowRequest.model_validate(request_args(**overrides))
    _tris, (lo, hi) = cb.load_surfaces(req, pipe_dir)
    built = cb.build_case_files(req, lo, hi, max_cells=500_000)
    built["req"] = req
    return built


class TestCaseFiles:
    def test_the_solver_is_foamrun_with_the_incompressible_module(self, pipe: Path) -> None:
        text = build(pipe)["files"]["system/controlDict"]
        assert "application     foamRun;" in text
        assert "solver          incompressibleFluid;" in text

    def test_the_mesh_dictionary_uses_foundation_syntax(self, pipe: Path) -> None:
        text = build(pipe)["files"]["system/snappyHexMeshDict"]
        # OpenFOAM Foundation spells it insidePoint; ESI's locationInMesh is a different fork.
        assert "insidePoint (0.2 0 0);" in text
        assert "locationInMesh" not in text
        for name in ("inlet", "outlet", "wall0"):
            assert f'file "{name}.stl";' in text

    def test_inside_point_is_scaled_from_millimetres(self, pipe: Path) -> None:
        built = build(pipe, stl_units="mm", inside_point=[200.0, 0.0, 0.0])
        assert built["plan"]["inside_point_m"] == [0.2, 0.0, 0.0]

    def test_the_inlet_velocity_is_negative_along_the_outward_normal(self, pipe: Path) -> None:
        text = build(pipe)["files"]["0/U"]
        assert "surfaceNormalFixedValue" in text
        assert "refValue        uniform -0.025;" in text

    def test_the_outlet_is_fixed_at_zero_gauge_pressure(self, pipe: Path) -> None:
        text = build(pipe)["files"]["0/p"]
        assert "outlet" in text and "fixedValue" in text

    def test_laminar_has_no_turbulence_fields(self, pipe: Path) -> None:
        built = build(pipe)
        assert "0/k" not in built["files"] and "0/omega" not in built["files"]
        assert "simulationType laminar;" in built["files"]["constant/momentumTransport"]

    def test_k_omega_sst_derives_inlet_k_and_omega_from_the_inputs(self, pipe: Path) -> None:
        built = build(
            pipe,
            turbulence="kOmegaSST",
            inlet_velocity_m_s=3.0,
            turbulence_intensity=0.05,
            turbulence_length_scale_m=0.0014,
        )
        assert "model           kOmegaSST;" in built["files"]["constant/momentumTransport"]
        k = 1.5 * (0.05 * 3.0) ** 2
        assert f"uniform {k:.9g}" in built["files"]["0/k"]
        assert "omegaWallFunction" in built["files"]["0/omega"]
        assert "nutkWallFunction" in built["files"]["0/nut"]
        assert "#includeFunc yPlus" in built["files"]["system/controlDict"]

    def test_the_fluid_viscosity_is_written_as_given(self, pipe: Path) -> None:
        text = build(pipe)["files"]["constant/physicalProperties"]
        assert "nu              1e-05;" in text

    def test_the_cell_size_is_defaulted_and_says_so(self, pipe: Path) -> None:
        plan = build(pipe)["plan"]
        assert plan["cell_size_defaulted"] is True
        assert plan["background_cell_size_m"] == pytest.approx(0.4 / 50)

    def test_an_explicit_cell_size_is_not_reported_as_defaulted(self, pipe: Path) -> None:
        plan = build(pipe, mesh={"cell_size_m": 0.004})["plan"]
        assert plan["cell_size_defaulted"] is False
        assert plan["background_cell_size_m"] == 0.004

    def test_parallel_runs_get_a_decompose_dictionary(self, pipe: Path) -> None:
        files = build(pipe, solver={"n_procs": 4})["files"]
        assert "numberOfSubdomains 4;" in files["system/decomposeParDict"]
        assert "system/decomposeParDict" not in build(pipe)["files"]

    def test_several_walls_get_their_own_patches(self, tmp_path: Path) -> None:
        paths = write_pipe(tmp_path, 0.01, 0.4, 16)
        (tmp_path / "wall2.stl").write_text(paths["wall"].read_text())
        built = build(tmp_path, wall_stls=["wall.stl", "wall2.stl"])
        text = built["files"]["system/snappyHexMeshDict"]
        assert 'file "wall0.stl";' in text and 'file "wall1.stl";' in text

    def test_the_residual_target_reaches_the_solution_dictionary(self, pipe: Path) -> None:
        text = build(pipe, solver={"residual_target": 1e-6})["files"]["system/fvSolution"]
        assert "p               1e-06;" in text and "consistent      yes;" in text


# ---------------------------------------------------------------------------
# Parsers, against files captured from a real OpenFOAM 11 run
# ---------------------------------------------------------------------------


class TestParsers:
    def test_check_mesh(self) -> None:
        mesh = results.parse_check_mesh((FIXTURES / "checkMesh.log").read_text())
        assert mesh["cells"] == 13784
        assert mesh["mesh_ok"] is True
        assert mesh["max_non_orthogonality_deg"] == pytest.approx(47.492896)
        assert mesh["max_skewness"] == pytest.approx(1.0311603)

    def test_the_fatal_message_is_lifted_from_the_block(self) -> None:
        text = (
            "Create mesh\n\n--> FOAM FATAL ERROR: \nPoint (0.2 0.5 0) is not inside the mesh\n"
            "or on a face or edge.\nBounding box of the mesh:(0 0 0) (1 1 1)\n\n"
            "    From function Foam::findCells\n    in file x.C at line 148.\n\nFOAM exiting\n"
        )
        assert results.fatal_message(text) == (
            "Point (0.2 0.5 0) is not inside the mesh or on a face or edge. "
            "Bounding box of the mesh:(0 0 0) (1 1 1)"
        )
        assert results.fatal_message("Finished meshing without any errors") is None

    def test_failed_mesh_checks_are_not_ok(self) -> None:
        mesh = results.parse_check_mesh("    cells: 10\nFailed 2 mesh checks.\n")
        assert mesh["mesh_ok"] is False and mesh["failed_checks"] == 2

    def test_boundary_patch_face_counts(self) -> None:
        patches = results.parse_boundary((FIXTURES / "boundary.txt").read_text())
        assert patches == {"inlet": 220, "outlet": 220, "wall0": 5984}

    def test_a_converged_solver_log(self) -> None:
        log = results.parse_solver_log((FIXTURES / "solver_converged.log").read_text())
        assert log["converged"] is True
        assert log["iterations"] == 121
        assert set(log["residuals"]) >= {"Ux", "p"}
        assert log["residuals"]["p"] < 1e-6

    def test_a_run_that_hit_its_iteration_limit_is_not_converged(self) -> None:
        log = results.parse_solver_log((FIXTURES / "solver_not_converged.log").read_text())
        assert log["converged"] is False
        assert log["iterations"] == 3000
        # The transverse velocity residuals stall near 0.1 in straight-pipe flow.
        assert log["residuals"]["Uy"] > 0.05

    def test_patch_average_columns_and_area_header(self) -> None:
        path = FIXTURES / "patch_average_inlet.dat"
        last = results.read_series(path)[-1]
        assert last[0] == 121 and last[1] == pytest.approx(8.10688108e-03)
        assert last[2] == pytest.approx(2.48274713e-02)  # Ux of the vector
        assert results.header_value(path, "Area") == pytest.approx(3.09647485e-04)

    def test_flow_rate_sign_convention(self) -> None:
        inlet = results.read_series(FIXTURES / "patch_flow_inlet.dat")[-1][1]
        outlet = results.read_series(FIXTURES / "patch_flow_outlet.dat")[-1][1]
        assert inlet < 0 < outlet

    def test_y_plus(self) -> None:
        yp = results.read_y_plus(FIXTURES / "yplus.dat")
        assert yp == {
            "min": pytest.approx(1.94733177),
            "max": pytest.approx(20.6273758),
            "average": pytest.approx(13.8980923),
        }

    def test_drift_is_small_when_the_value_has_settled(self) -> None:
        series = [8.10688114e-03, 8.10688113e-03, 8.10688112e-03] * 5
        assert results.drift(series) < 1e-6

    def test_drift_is_large_while_the_value_is_still_moving(self) -> None:
        assert results.drift([float(i) for i in range(1, 101)]) > 0.05

    def test_find_dat_picks_the_newest_time_directory(self, tmp_path: Path) -> None:
        for t in ("0", "200"):
            d = tmp_path / "postProcessing" / "patchFlowRate(patch=inlet)" / t
            d.mkdir(parents=True)
            (d / "surfaceFieldValue.dat").write_text(f"# t\n{t} 1\n")
        found = results.find_dat(tmp_path, "patchFlowRate(patch=inlet)", "surfaceFieldValue.dat")
        assert found is not None and found.parent.name == "200"


# ---------------------------------------------------------------------------
# The adapter, with a fake OpenFOAM
# ---------------------------------------------------------------------------

FAKE_BASHRC = r"""
blockMesh() { echo "blockMesh ok"; }
snappyHexMesh() {
  if [ "$FAKE_MODE" = snappy_fail ]; then printf -- '--> FOAM FATAL ERROR: \nPoint (0.2 0.5 0) is not inside the mesh or on a face or edge.\n\n    From function findCells\n'; return 1; fi
  mkdir -p constant/polyMesh
  if [ "$FAKE_MODE" = leak ]; then cp "$FAKE_DIR/boundary_leak.txt" constant/polyMesh/boundary
  elif [ "$FAKE_MODE" = no_outlet ]; then cp "$FAKE_DIR/boundary_no_outlet.txt" constant/polyMesh/boundary
  else cp "$FAKE_DIR/boundary.txt" constant/polyMesh/boundary; fi
}
checkMesh() { cat "$FAKE_DIR/checkMesh.log"; }
decomposePar() { echo decomposed; }
reconstructPar() { echo reconstructed; }
mpirun() { echo "mpirun $*" >> mpirun.args; foamRun; }
foamRun() {
  if [ "$FAKE_MODE" = slow ]; then sleep 30; fi
  p=postProcessing
  for pair in \
    "patchAverage(patch=inlet,fields=(pU))|surfaceFieldValue.dat|patch_average_inlet.dat" \
    "patchAverage(patch=outlet,fields=(pU))|surfaceFieldValue.dat|patch_average_outlet.dat" \
    "patchDifference(patch1=inlet,patch2=outlet,fields=(p))|fieldValueDelta.dat|patch_difference.dat" \
    "patchFlowRate(patch=inlet)|surfaceFieldValue.dat|patch_flow_inlet.dat" \
    "patchFlowRate(patch=outlet)|surfaceFieldValue.dat|patch_flow_outlet.dat"; do
    IFS='|' read -r dir file src <<< "$pair"
    mkdir -p "$p/$dir/0"; cp "$FAKE_DIR/$src" "$p/$dir/0/$file"
  done
  if [ "$FAKE_MODE" = not_converged ]; then cat "$FAKE_DIR/solver_not_converged.log"
  else cat "$FAKE_DIR/solver_converged.log"; fi
}
"""  # noqa: E501


@pytest.fixture
def fake_openfoam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A work directory with a pipe in it and a fake OpenFOAM environment."""
    work = tmp_path / "work"
    write_pipe(work, radius=0.01, length=0.4, segments=24)
    bashrc = tmp_path / "bashrc"
    bashrc.write_text(FAKE_BASHRC)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for f in FIXTURES.iterdir():
        (fixtures / f.name).write_bytes(f.read_bytes())
    boundary = (FIXTURES / "boundary.txt").read_text()
    leak = boundary.replace("3\n(", "4\n(").replace(
        "\n)\n",
        "\n    background\n    {\n        type            patch;\n        nFaces          120;\n"
        "        startFace       1;\n    }\n)\n",
    )
    (fixtures / "boundary_leak.txt").write_text(leak)
    no_outlet = boundary.replace(
        "nFaces          220;\n        startFace       39291;",
        "nFaces          0;\n        startFace       39291;",
    )
    (fixtures / "boundary_no_outlet.txt").write_text(no_outlet)
    monkeypatch.setenv("FAKE_DIR", str(fixtures))
    monkeypatch.setenv("FAKE_MODE", "ok")
    work.joinpath("bashrc_path").write_text(str(bashrc))
    return work


def server_for(work: Path, **cfg: object) -> OpenFoamServer:
    bashrc = Path((work / "bashrc_path").read_text())
    return OpenFoamServer(OpenFoamConfig(bashrc=str(bashrc), work_dir=str(work), **cfg))


class TestAdapter:
    async def test_a_converged_run_returns_the_solver_numbers(self, fake_openfoam: Path) -> None:
        out = await server_for(fake_openfoam).run_internal_flow(request_args())
        assert out["completed"] is True and out["converged"] is True
        assert out["iterations"] == 121
        # kinematic 8.10688108e-03 m^2/s^2 times the density 1.2
        assert out["pressure_drop_pa"] == pytest.approx(8.10688108e-03 * 1.2)
        assert out["solution_stable"] is True
        assert out["mesh"]["cells"] == 13784 and out["mesh"]["mesh_ok"] is True
        assert out["inlet"]["volumetric_flow_m3_s"] == pytest.approx(7.74118713e-06)
        assert out["inlet"]["mass_flow_kg_s"] == pytest.approx(7.74118713e-06 * 1.2)
        assert out["outlet"]["volumetric_flow_m3_s"] == pytest.approx(7.74118729e-06)
        assert out["inlet"]["area_m2"] == pytest.approx(3.09647485e-04)
        assert out["flow_balance_error"] < 1e-6
        assert out["warnings"] == []
        assert set(out["timings_s"]) >= {"blockMesh", "snappyHexMesh", "checkMesh", "foamRun"}

    async def test_the_case_is_left_on_the_workspace_for_inspection(
        self, fake_openfoam: Path
    ) -> None:
        out = await server_for(fake_openfoam).run_internal_flow(request_args())
        case = Path(out["case_dir"])
        assert case.is_relative_to(fake_openfoam / "openfoam")
        assert (case / "system" / "snappyHexMeshDict").is_file()
        assert (case / "constant" / "geometry" / "inlet.stl").is_file()
        assert (case / "log.foamRun").is_file()

    async def test_a_run_that_does_not_converge_says_so(
        self, fake_openfoam: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "not_converged")
        out = await server_for(fake_openfoam).run_internal_flow(request_args())
        assert out["completed"] is True and out["converged"] is False
        assert any("residual controls were not met" in w for w in out["warnings"])

    async def test_a_mesher_failure_reports_the_stage_and_the_log(
        self, fake_openfoam: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "snappy_fail")
        out = await server_for(fake_openfoam).run_internal_flow(request_args())
        assert out["completed"] is False
        assert out["stage_failed"] == "snappyHexMesh"
        assert "is not inside the mesh" in out["log"]
        # OpenFOAM's own reason is lifted into the error, not left in the log.
        assert out["error"].endswith(
            "Point (0.2 0.5 0) is not inside the mesh or on a face or edge."
        )
        assert "pressure_drop_pa" not in out

    async def test_surfaces_that_leak_are_reported_not_solved(
        self, fake_openfoam: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "leak")
        out = await server_for(fake_openfoam).run_internal_flow(request_args())
        assert out["completed"] is False and out["stage_failed"] == "snappyHexMesh"
        assert "leak" in out["error"]
        assert "foamRun" not in out["timings_s"]

    async def test_a_patch_with_no_faces_means_the_fluid_was_not_bounded(
        self, fake_openfoam: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "no_outlet")
        out = await server_for(fake_openfoam).run_internal_flow(request_args())
        assert out["completed"] is False
        assert "no mesh faces on outlet" in out["error"]

    async def test_a_run_over_its_time_budget_is_killed_and_says_timed_out(
        self, fake_openfoam: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "slow")
        out = await server_for(fake_openfoam, max_operation_time=2).run_internal_flow(
            request_args()
        )
        assert out["completed"] is False
        assert out["stage_failed"] == "foamRun" and out["error"] == "timed out"

    async def test_a_mesh_over_the_cell_cap_is_refused(self, fake_openfoam: Path) -> None:
        out = await server_for(fake_openfoam, max_cells=5000).run_internal_flow(request_args())
        # The fake mesh has 13784 cells, over a 5000 cap; the background box fits under it.
        assert out["completed"] is False and out["stage_failed"] == "checkMesh"
        assert "cap" in out["error"]

    async def test_a_background_mesh_over_the_cap_is_refused_before_any_run(
        self, fake_openfoam: Path
    ) -> None:
        with pytest.raises(ValueError, match="background mesh"):
            await server_for(fake_openfoam, max_cells=2000).run_internal_flow(
                request_args(mesh={"cell_size_m": 0.0005})
            )

    async def test_too_many_processes_are_refused(self, fake_openfoam: Path) -> None:
        with pytest.raises(ValueError, match="n_procs"):
            await server_for(fake_openfoam, max_procs=2).run_internal_flow(
                request_args(solver={"n_procs": 4})
            )

    async def test_a_parallel_run_decomposes_runs_under_mpirun_and_reconstructs(
        self, fake_openfoam: Path
    ) -> None:
        out = await server_for(fake_openfoam).run_internal_flow(request_args(solver={"n_procs": 3}))
        assert out["completed"] is True
        assert {"decomposePar", "foamRun", "reconstructPar"} <= set(out["timings_s"])
        args = (Path(out["case_dir"]) / "mpirun.args").read_text()
        assert "-np 3 foamRun -parallel" in args

    async def test_two_runs_do_not_overlap(self, fake_openfoam: Path) -> None:
        import asyncio

        server = server_for(fake_openfoam)
        first, second = await asyncio.gather(
            server.run_internal_flow(request_args()), server.run_internal_flow(request_args())
        )
        assert first["completed"] and second["completed"]
        assert first["case_dir"] != second["case_dir"]

    async def test_the_tool_is_registered_with_a_real_schema(self, fake_openfoam: Path) -> None:
        server = server_for(fake_openfoam)
        assert "openfoam.run_internal_flow" in server.tool_ids
        schema = cb.FlowRequest.model_json_schema()
        assert {"inlet_stl", "outlet_stl", "wall_stls", "fluid", "turbulence"} <= set(
            schema["properties"]
        )
        json.dumps(schema)  # serialisable for tools/list
