"""OpenFOAM flow solver adapter, ``openfoam.run_internal_flow`` (FORGE-557).

``run_cfd`` could only answer steady conduction: it refused velocity, pressure
drop and convection because nothing on the server computes a flow. This adapter
runs OpenFOAM 11 (``blockMesh``, ``snappyHexMesh``, ``foamRun`` with the
``incompressibleFluid`` module) over surfaces the caller supplies, and reads the
pressure drop, flow rates and mesh quality back from the solver's own output.
Nothing is estimated: a run that fails says at which stage and shows the log, and
a run that does not converge says so rather than returning its last iterate as if
it were an answer.

Scope of this first tool: steady, incompressible, single-phase internal flow,
laminar or k-omega SST, through a domain the caller bounds with an inlet surface,
an outlet surface and wall surfaces. Compressible flow, heat transfer, rotating
machinery and layer meshing are not here; the tool does not pretend to them.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.openfoam import case_builder as cb
from tool_registry.tools.openfoam import results, stl
from tool_registry.tools.openfoam.config import OpenFoamConfig
from tool_registry.tools.openfoam.runner import StageResult, log_tail, run_stage

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.openfoam.adapter")

#: A pressure drop that moved by less than this over the last tenth of the run is
#: reported as stable. Reported, not assumed: the number is in the result too.
STABLE_DRIFT = 0.01
#: Inlet and outlet volumetric flow should match; more than this is worth a warning.
FLOW_BALANCE_WARN = 0.01


class OpenFoamNotAvailableError(RuntimeError):
    """OpenFOAM is not installed where this adapter runs."""


class OpenFoamServer(McpToolServer):
    """Serves ``openfoam.run_internal_flow``."""

    def __init__(self, config: OpenFoamConfig | None = None) -> None:
        super().__init__(adapter_id="openfoam", version="0.1.0")
        self.config = config or OpenFoamConfig()
        # One run at a time. The adapter shares a host with the twin and its
        # databases; two meshing jobs at once would starve them.
        self._lock = asyncio.Semaphore(1)
        self.register_tool(
            manifest=ToolManifest(
                tool_id="openfoam.run_internal_flow",
                adapter_id="openfoam",
                name="Run Internal Flow (OpenFOAM)",
                description=(
                    "Steady incompressible internal flow with OpenFOAM 11. Give the fluid "
                    "domain as three kinds of STL surface on the adapter workspace: `inlet_stl`, "
                    "`outlet_stl` and `wall_stls` (together they must close), plus "
                    "`stl_units` (m or mm) and `inside_point` (a point inside the fluid, in "
                    "those units). Give the `fluid` (density and kinematic viscosity, both "
                    "required), the `inlet_velocity_m_s` (uniform, normal to the inlet) and "
                    "`turbulence`: laminar, or kOmegaSST with `turbulence_intensity` and "
                    "`turbulence_length_scale_m`. Meshes with snappyHexMesh, solves with "
                    "foamRun and returns the static pressure drop inlet to outlet (Pa), flow "
                    "rate and mass flow, mesh quality, final residuals, whether the residual "
                    "controls were met, and how much the pressure drop moved over the end of "
                    "the run. A run that fails reports its stage and log; one that does not "
                    "converge says so. Refuses compressible flow, heat transfer and rotating "
                    "parts: it does not model them."
                ),
                capability="flow_simulation",
                input_schema=cb.FlowRequest.model_json_schema(),
                output_schema={
                    "type": "object",
                    "properties": {
                        "completed": {"type": "boolean"},
                        "converged": {"type": "boolean"},
                        "solution_stable": {"type": ["boolean", "null"]},
                        "pressure_drop_pa": {"type": ["number", "null"]},
                        "iterations": {"type": "integer"},
                        "residuals": {"type": "object"},
                        "inlet": {"type": "object"},
                        "outlet": {"type": "object"},
                        "mesh": {"type": "object"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                        "case_dir": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=12288, max_cpu_seconds=3600, max_disk_mb=4096
                ),
            ),
            handler=self.run_internal_flow,
        )

    async def run_internal_flow(self, arguments: dict[str, Any]) -> dict[str, Any]:
        req = cb.FlowRequest.model_validate(arguments)
        cfg = self.config
        if req.solver.n_procs > cfg.max_procs:
            raise ValueError(f"n_procs {req.solver.n_procs} exceeds this adapter's {cfg.max_procs}")

        work = Path(cfg.work_dir)
        tris, (lo, hi) = cb.load_surfaces(req, work)
        built = cb.build_case_files(req, lo, hi, cfg.max_cells)
        plan = built["plan"]
        bg = plan["background_cells"]
        if bg[0] * bg[1] * bg[2] > cfg.max_cells:
            raise ValueError(
                f"the background mesh alone would be {bg[0] * bg[1] * bg[2]} cells, over the "
                f"{cfg.max_cells} cap: use a larger mesh.cell_size_m"
            )

        run_id = uuid.uuid4().hex[:12]
        case_dir = work / "openfoam" / run_id / "case"
        self._write_case(case_dir, req, tris, built["files"])

        with tracer.start_as_current_span("openfoam.run_internal_flow") as span:
            span.set_attribute("openfoam.turbulence", req.turbulence)
            span.set_attribute("openfoam.n_procs", req.solver.n_procs)
            async with self._lock:
                out = await self._run(req, case_dir)
            out["plan"] = plan
            out["case_dir"] = str(case_dir)
            out["run_id"] = run_id
            span.set_attribute("openfoam.completed", bool(out.get("completed")))
            span.set_attribute("openfoam.converged", bool(out.get("converged")))
            logger.info(
                "openfoam_run",
                run_id=run_id,
                completed=out.get("completed"),
                converged=out.get("converged"),
                stage_failed=out.get("stage_failed"),
                cells=(out.get("mesh") or {}).get("cells"),
            )
            return out

    @staticmethod
    def _write_case(
        case_dir: Path,
        req: cb.FlowRequest,
        tris: dict[str, list[stl.Triangle]],
        files: dict[str, str],
    ) -> None:
        geometry = case_dir / "constant" / "geometry"
        geometry.mkdir(parents=True, exist_ok=True)
        for patch, ts in tris.items():
            stl.write_ascii_stl(geometry / f"{patch}.stl", patch, ts, req.scale)
        for rel, text in files.items():
            path = case_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    async def _run(self, req: cb.FlowRequest, case_dir: Path) -> dict[str, Any]:
        cfg = self.config
        if not Path(cfg.bashrc).is_file():
            raise OpenFoamNotAvailableError(
                f"{cfg.bashrc} not found; run the openfoam-adapter container"
            )
        deadline = time.monotonic() + cfg.max_operation_time
        timings: dict[str, float] = {}
        n = req.solver.n_procs

        def failed(stage: StageResult, why: str) -> dict[str, Any]:
            # OpenFOAM states its reason in a FOAM FATAL ERROR block; put it in
            # the error so a reader does not have to open the log for it.
            reason = results.fatal_message(log_tail(case_dir, stage.name, 200))
            if stage.timed_out:
                error = "timed out"
            else:
                error = f"{why}: {reason}" if reason else why
            return {
                "completed": False,
                "converged": False,
                "stage_failed": stage.name,
                "error": error,
                "log": log_tail(case_dir, stage.name),
                "timings_s": timings,
            }

        async def stage(name: str, argv: list[str]) -> StageResult:
            res = await run_stage(cfg.bashrc, case_dir, name, argv, deadline - time.monotonic())
            timings[name] = res.seconds
            return res

        for name, argv in (
            ("blockMesh", ["blockMesh"]),
            ("snappyHexMesh", ["snappyHexMesh", "-overwrite"]),
        ):
            res = await stage(name, argv)
            if not res.ok:
                return failed(res, f"{name} exited with code {res.returncode}")

        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        patches = (
            results.parse_boundary(boundary_path.read_text()) if boundary_path.is_file() else {}
        )
        problem = self._patch_problem(req, patches)
        if problem:
            return {
                "completed": False,
                "converged": False,
                "stage_failed": "snappyHexMesh",
                "error": problem,
                "log": log_tail(case_dir, "snappyHexMesh"),
                "mesh": {"patch_faces": patches},
                "timings_s": timings,
            }

        res = await stage("checkMesh", ["checkMesh"])
        check_text = (
            (case_dir / "log.checkMesh").read_text(errors="replace")
            if (case_dir / "log.checkMesh").is_file()
            else ""
        )
        mesh = results.parse_check_mesh(check_text)
        mesh["patch_faces"] = patches
        if not res.ok:
            out = failed(res, "checkMesh found errors in the mesh")
            out["mesh"] = mesh
            return out
        if mesh["cells"] is not None and mesh["cells"] > cfg.max_cells:
            return {
                "completed": False,
                "converged": False,
                "stage_failed": "checkMesh",
                "error": f"the mesh has {mesh['cells']} cells, over the {cfg.max_cells} cap: "
                "use a larger mesh.cell_size_m or less surface refinement",
                "mesh": mesh,
                "timings_s": timings,
            }

        if n > 1:
            steps: list[tuple[str, list[str]]] = [
                ("decomposePar", ["decomposePar", "-force"]),
                (
                    "foamRun",
                    [
                        "mpirun",
                        *(["--oversubscribe"] if n > (os.cpu_count() or 1) else []),
                        "-np",
                        str(n),
                        "foamRun",
                        "-parallel",
                    ],
                ),
                ("reconstructPar", ["reconstructPar", "-latestTime"]),
            ]
        else:
            steps = [("foamRun", ["foamRun"])]
        for name, argv in steps:
            res = await stage(name, argv)
            if not res.ok:
                out = failed(res, f"{name} exited with code {res.returncode}")
                out["mesh"] = mesh
                return out

        return self._collect(req, case_dir, mesh, timings)

    @staticmethod
    def _patch_problem(req: cb.FlowRequest, patches: dict[str, int]) -> str | None:
        """The surfaces must close around the fluid; a leak shows up as missing
        patches or faces on the background box."""
        wanted = ["inlet", "outlet", *cb.wall_patch_names(len(req.wall_stls))]
        empty = [p for p in wanted if patches.get(p, 0) == 0]
        if empty:
            return (
                f"no mesh faces on {', '.join(empty)}: the surfaces did not bound the fluid. "
                "Check inside_point is inside the fluid and the inlet, outlet and walls close"
            )
        if patches.get("background", 0) > 0:
            return (
                "the fluid reaches the edge of the background box: the surfaces leak. "
                "Close the gap between the inlet, outlet and walls"
            )
        return None

    def _collect(
        self,
        req: cb.FlowRequest,
        case_dir: Path,
        mesh: dict[str, Any],
        timings: dict[str, float],
    ) -> dict[str, Any]:
        rho = req.fluid.density_kg_m3
        solver_log = results.parse_solver_log(
            (case_dir / "log.foamRun").read_text(errors="replace")
        )
        warnings: list[str] = []
        if not mesh["mesh_ok"]:
            warnings.append("checkMesh reported failed quality checks")
        if not solver_log["converged"]:
            warnings.append(
                f"the residual controls were not met in {solver_log['iterations']} iterations"
            )

        def patch(name: str, into_domain: bool) -> dict[str, Any]:
            """OpenFOAM's flux is positive out of the domain, so the inlet reads
            negative. Reported the other way round: both flows are positive when
            the fluid goes the way the patch's name says."""
            sign = -1.0 if into_domain else 1.0
            avg = results.find_dat(case_dir, f"patchAverage(patch={name},", "surfaceFieldValue.dat")
            flow = results.find_dat(
                case_dir, f"patchFlowRate(patch={name})", "surfaceFieldValue.dat"
            )
            out: dict[str, Any] = {}
            if avg:
                rows = results.read_series(avg)
                if rows:
                    p_kin, ux, uy, uz = rows[-1][1], rows[-1][2], rows[-1][3], rows[-1][4]
                    out["static_pressure_pa"] = p_kin * rho
                    out["mean_velocity_m_s"] = [ux, uy, uz]
                area = results.header_value(avg, "Area")
                if area is not None:
                    out["area_m2"] = area
            if flow:
                rows = results.read_series(flow)
                if rows:
                    q = sign * rows[-1][1]
                    out["volumetric_flow_m3_s"] = q
                    out["mass_flow_kg_s"] = q * rho
            return out

        inlet, outlet = patch("inlet", into_domain=True), patch("outlet", into_domain=False)

        dp: float | None = None
        drift: float | None = None
        diff = results.find_dat(
            case_dir, "patchDifference(patch1=inlet,patch2=outlet", "fieldValueDelta.dat"
        )
        if diff:
            series = [row[1] for row in results.read_series(diff)]
            if series:
                dp = series[-1] * rho
                drift = results.drift(series)

        balance: float | None = None
        qi, qo = inlet.get("volumetric_flow_m3_s"), outlet.get("volumetric_flow_m3_s")
        if qi and qo is not None:
            balance = abs(qi - qo) / abs(qi)
            if balance > FLOW_BALANCE_WARN:
                warnings.append(f"inlet and outlet flow differ by {balance:.1%}")

        y_plus = None
        yp = results.find_dat(case_dir, "yPlus", "yPlus.dat")
        if yp:
            y_plus = results.read_y_plus(yp)
            if y_plus and y_plus["max"] > 300:
                warnings.append(f"y+ reaches {y_plus['max']:.0f}, above the wall-function range")

        stable = None if drift is None else drift < STABLE_DRIFT
        if dp is None:
            warnings.append("no pressure drop was written")
        return {
            "completed": True,
            "converged": solver_log["converged"],
            "solution_stable": stable,
            "pressure_drop_pa": dp,
            "pressure_drop_drift": drift,
            "iterations": solver_log["iterations"],
            "residuals": solver_log["residuals"],
            "inlet": inlet,
            "outlet": outlet,
            "flow_balance_error": balance,
            "mesh": mesh,
            "y_plus": y_plus,
            "warnings": warnings,
            "assumptions": {
                "fluid": "Newtonian, incompressible, constant properties as given",
                "pressure": "static, area-averaged on the inlet and outlet patches; "
                "kinematic OpenFOAM p multiplied by the given density",
                "inlet": "uniform speed normal to the inlet patch",
                "flows": "positive into the domain at the inlet and out of it at the outlet",
                "outlet": "fixed zero gauge pressure",
                "turbulence": req.turbulence,
            },
            "timings_s": timings,
        }
