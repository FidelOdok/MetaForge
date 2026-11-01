"""Build an OpenFOAM 11 case for one steady internal flow (FORGE-557).

Everything here is a pure function from a validated request to file contents,
so the case can be tested without OpenFOAM. The syntax is OpenFOAM
Foundation 11: ``foamRun`` with the ``incompressibleFluid`` solver module, a
``SIMPLE`` dictionary with ``consistent yes`` (SIMPLEC), and ``insidePoint`` in
the snappyHexMesh dictionary (ESI's ``locationInMesh`` is a different fork).
The files were checked against the ``pitzDailySteady`` and ``motorBikeSteady``
tutorials that ship in the image.

Nothing is defaulted silently. A fluid has to say its density and viscosity, a
turbulent run its intensity and length scale, an STL its units. The numerical
method settings that do have defaults (iteration count, residual target,
refinement levels, background cell size) are returned in the result so a reader
sees what was used.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tool_registry.tools.openfoam import stl

Vec3 = tuple[float, float, float]

_HEADER = (
    "FoamFile\n{\n    format      ascii;\n    class       %(cls)s;\n"
    '    location    "%(loc)s";\n    object      %(obj)s;\n}\n\n'
)

# The k-omega SST closure constant used to turn a length scale into omega.
_CMU_QUARTER = 0.09**0.25


class Fluid(BaseModel):
    """A Newtonian, incompressible fluid. Both figures are required: an air
    density used for water would be a quietly wrong answer."""

    density_kg_m3: float = Field(gt=0, description="Density, kg/m^3")
    kinematic_viscosity_m2_s: float = Field(gt=0, description="Kinematic viscosity, m^2/s")


class MeshSettings(BaseModel):
    cell_size_m: float | None = Field(
        default=None,
        gt=0,
        description="Background cell size, m. Omitted: the longest extent divided by 50",
    )
    surface_refinement_min: int = Field(default=1, ge=0, le=6)
    surface_refinement_max: int = Field(default=2, ge=0, le=6)

    @model_validator(mode="after")
    def _ordered(self) -> MeshSettings:
        if self.surface_refinement_max < self.surface_refinement_min:
            raise ValueError("surface_refinement_max must be at least surface_refinement_min")
        return self


class SolverSettings(BaseModel):
    max_iterations: int = Field(default=1000, ge=1, le=100_000)
    residual_target: float = Field(default=1e-5, gt=0, lt=1)
    n_procs: int = Field(default=1, ge=1)


class FlowRequest(BaseModel):
    """One steady internal-flow run: surfaces in, pressure drop and flow out."""

    inlet_stl: str
    outlet_stl: str
    wall_stls: list[str] = Field(min_length=1)
    stl_units: Literal["m", "mm"]
    inside_point: Vec3 = Field(description="A point inside the fluid, in the STL units")
    fluid: Fluid
    inlet_velocity_m_s: float = Field(gt=0, description="Uniform speed, normal to the inlet")
    turbulence: Literal["laminar", "kOmegaSST"]
    turbulence_intensity: float | None = Field(default=None, gt=0, le=1)
    turbulence_length_scale_m: float | None = Field(default=None, gt=0)
    mesh: MeshSettings = Field(default_factory=MeshSettings)
    solver: SolverSettings = Field(default_factory=SolverSettings)

    @model_validator(mode="after")
    def _turbulent_needs_inlet_values(self) -> FlowRequest:
        if self.turbulence == "kOmegaSST" and (
            self.turbulence_intensity is None or self.turbulence_length_scale_m is None
        ):
            raise ValueError(
                "kOmegaSST needs turbulence_intensity (0 to 1) and turbulence_length_scale_m: "
                "they set the inlet k and omega and nothing is assumed for them"
            )
        return self

    @property
    def scale(self) -> float:
        return 1.0 if self.stl_units == "m" else 0.001


class Surfaces:
    """The three roles of surface, read, scaled to metres and named for patches."""

    def __init__(self, inlet: str, outlet: str, walls: list[str]) -> None:
        self.inlet, self.outlet, self.walls = inlet, outlet, walls

    @property
    def patch_names(self) -> list[str]:
        return [self.inlet, self.outlet, *self.walls]


def wall_patch_names(count: int) -> list[str]:
    """``wall0``, ``wall1`` ...: the boundary conditions match ``wall.*``."""
    return [f"wall{i}" for i in range(count)]


def resolve_inside(work_dir: Path, name: str) -> Path:
    """A file under the adapter workspace; anything that escapes it is refused."""
    root = work_dir.resolve()
    path = Path(name)
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{name!r} is outside the adapter workspace")
    if not resolved.is_file():
        raise ValueError(f"surface not found on the adapter workspace: {name}")
    return resolved


def load_surfaces(
    req: FlowRequest, work_dir: Path
) -> tuple[dict[str, list[stl.Triangle]], tuple[Vec3, Vec3]]:
    """Every surface keyed by its patch name, and their joint bounds in metres."""
    sources = {"inlet": req.inlet_stl, "outlet": req.outlet_stl}
    for patch, src in zip(wall_patch_names(len(req.wall_stls)), req.wall_stls, strict=True):
        sources[patch] = src
    tris: dict[str, list[stl.Triangle]] = {}
    for patch, src in sources.items():
        tris[patch] = stl.read_stl(resolve_inside(work_dir, src))
    all_tris = [t for ts in tris.values() for t in ts]
    lo, hi = stl.bounds(all_tris)
    s = req.scale
    return tris, (
        (lo[0] * s, lo[1] * s, lo[2] * s),
        (hi[0] * s, hi[1] * s, hi[2] * s),
    )


def background_plan(
    lo: Vec3, hi: Vec3, cell_size: float | None
) -> tuple[Vec3, Vec3, tuple[int, int, int], float]:
    """The blockMesh box around the surfaces: a small margin, square-ish cells."""
    extent = tuple(h - low for low, h in zip(lo, hi, strict=True))
    longest = max(extent)
    if longest <= 0:
        raise ValueError("the surfaces have no extent")
    size = cell_size if cell_size is not None else longest / 50.0
    margin = max(size * 2, longest * 0.02)
    blo = tuple(low - margin for low in lo)
    bhi = tuple(h + margin for h in hi)
    cells = tuple(max(4, math.ceil((h - low) / size)) for low, h in zip(blo, bhi, strict=True))
    return blo, bhi, cells, size  # type: ignore[return-value]


def _hdr(cls: str, loc: str, obj: str) -> str:
    return _HEADER % {"cls": cls, "loc": loc, "obj": obj}


def _vec(v: Vec3) -> str:
    return f"({v[0]:.9g} {v[1]:.9g} {v[2]:.9g})"


def block_mesh_dict(blo: Vec3, bhi: Vec3, cells: tuple[int, int, int]) -> str:
    x0, y0, z0 = blo
    x1, y1, z1 = bhi
    verts = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    vtext = "\n".join(f"    {_vec(v)}" for v in verts)
    return (
        _hdr("dictionary", "system", "blockMeshDict")
        + "convertToMeters 1;\n\n"
        + f"vertices\n(\n{vtext}\n);\n\n"
        + f"blocks\n(\n    hex (0 1 2 3 4 5 6 7) ({cells[0]} {cells[1]} {cells[2]}) "
        + "simpleGrading (1 1 1)\n);\n\n"
        + "defaultPatch\n{\n    name background;\n    type patch;\n}\n\n"
        + "boundary\n(\n);\n"
    )


def snappy_dict(req: FlowRequest, inside_m: Vec3, max_cells: int) -> str:
    walls = wall_patch_names(len(req.wall_stls))
    names = ["inlet", "outlet", *walls]
    lo, hi = req.mesh.surface_refinement_min, req.mesh.surface_refinement_max
    geometry = "\n".join(
        f'    {n}\n    {{\n        type triSurfaceMesh;\n        file "{n}.stl";\n    }}'
        for n in names
    )

    def surface(n: str) -> str:
        kind = "wall" if n.startswith("wall") else "patch"
        return (
            f"        {n}\n        {{\n            level ({lo} {hi});\n"
            f"            patchInfo {{ type {kind}; }}\n        }}"
        )

    surfaces = "\n".join(surface(n) for n in names)
    return (
        _hdr("dictionary", "system", "snappyHexMeshDict")
        + "castellatedMesh true;\nsnap            true;\naddLayers       false;\n\n"
        + f"geometry\n{{\n{geometry}\n}};\n\n"
        + "castellatedMeshControls\n{\n"
        + f"    maxLocalCells {max_cells};\n    maxGlobalCells {max_cells};\n"
        + "    minRefinementCells 0;\n    maxLoadUnbalance 0.10;\n    nCellsBetweenLevels 2;\n"
        + "    features ();\n"
        + f"    refinementSurfaces\n    {{\n{surfaces}\n    }}\n"
        + "    resolveFeatureAngle 30;\n    refinementRegions {}\n"
        + f"    insidePoint {_vec(inside_m)};\n    allowFreeStandingZoneFaces true;\n}}\n\n"
        + "snapControls\n{\n    nSmoothPatch 3;\n    tolerance 2.0;\n    nSolveIter 30;\n"
        + "    nRelaxIter 5;\n    nFeatureSnapIter 10;\n    implicitFeatureSnap false;\n"
        + "    explicitFeatureSnap false;\n    multiRegionFeatureSnap false;\n}\n\n"
        + "addLayersControls\n{\n    relativeSizes true;\n    layers {}\n    expansionRatio 1.0;\n"
        + "    finalLayerThickness 0.3;\n    minThickness 0.1;\n    nGrow 0;\n"
        + "    featureAngle 60;\n    nRelaxIter 3;\n    nSmoothSurfaceNormals 1;\n"
        + "    nSmoothNormals 3;\n    nSmoothThickness 10;\n    maxFaceThicknessRatio 0.5;\n"
        + "    maxThicknessToMedialRatio 0.3;\n    minMedianAxisAngle 90;\n"
        + "    nBufferCellsNoExtrude 0;\n    nLayerIter 50;\n}\n\n"
        + "meshQualityControls\n{\n    maxNonOrtho 65;\n    maxBoundarySkewness 20;\n"
        + "    maxInternalSkewness 4;\n    maxConcave 80;\n    minVol 1e-13;\n"
        + "    minTetQuality 1e-15;\n    minArea -1;\n    minTwist 0.02;\n"
        + "    minDeterminant 0.001;\n    minFaceWeight 0.05;\n    minVolRatio 0.01;\n"
        + "    minTriangleTwist -1;\n    nSmoothScale 4;\n    errorReduction 0.75;\n"
        + "    relaxed { maxNonOrtho 75; }\n}\n\n"
        + "writeFlags ();\nmergeTolerance 1e-6;\n"
    )


def control_dict(req: FlowRequest, functions: list[str]) -> str:
    n = req.solver.max_iterations
    funcs = "\n".join(f"    {f}" for f in functions)
    return (
        _hdr("dictionary", "system", "controlDict")
        + "application     foamRun;\nsolver          incompressibleFluid;\n\n"
        + "startFrom       startTime;\nstartTime       0;\nstopAt          endTime;\n"
        + f"endTime         {n};\ndeltaT          1;\nwriteControl    timeStep;\n"
        + f"writeInterval   {n};\npurgeWrite      0;\n"
        + "writeFormat     ascii;\nwritePrecision  8;\nwriteCompression off;\n"
        + "timeFormat      general;\ntimePrecision   6;\nrunTimeModifiable false;\n\n"
        + f"functions\n{{\n{funcs}\n}}\n"
    )


def fv_schemes() -> str:
    return (
        _hdr("dictionary", "system", "fvSchemes")
        + "ddtSchemes\n{\n    default         steadyState;\n}\n\n"
        + "gradSchemes\n{\n    default         Gauss linear;\n}\n\n"
        + "divSchemes\n{\n    default         none;\n"
        + "    div(phi,U)      bounded Gauss linearUpwind grad(U);\n"
        + "    div(phi,k)      bounded Gauss limitedLinear 1;\n"
        + "    div(phi,omega)  bounded Gauss limitedLinear 1;\n"
        + "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n\n"
        + "laplacianSchemes\n{\n    default         Gauss linear corrected;\n}\n\n"
        + "interpolationSchemes\n{\n    default         linear;\n}\n\n"
        + "snGradSchemes\n{\n    default         corrected;\n}\n\n"
        + "wallDist\n{\n    method meshWave;\n}\n"
    )


def fv_solution(req: FlowRequest) -> str:
    t = f"{req.solver.residual_target:.3g}"
    return (
        _hdr("dictionary", "system", "fvSolution")
        + "solvers\n{\n    p\n    {\n        solver          GAMG;\n"
        + "        tolerance       1e-08;\n"
        + "        relTol          0.1;\n        smoother        GaussSeidel;\n    }\n\n"
        + '    "(U|k|omega)"\n    {\n        solver          smoothSolver;\n'
        + "        smoother        symGaussSeidel;\n        tolerance       1e-08;\n"
        + "        relTol          0.1;\n    }\n}\n\n"
        + "SIMPLE\n{\n    nNonOrthogonalCorrectors 0;\n    consistent      yes;\n"
        + "    residualControl\n    {\n"
        + f"        p               {t};\n        U               {t};\n"
        + f'        "(k|omega)"     {t};\n    }}\n}}\n\n'
        + "relaxationFactors\n{\n    equations\n    {\n        U               0.9;\n"
        + '        ".*"            0.9;\n    }\n}\n'
    )


def decompose_par_dict(n_procs: int) -> str:
    return (
        _hdr("dictionary", "system", "decomposeParDict")
        + f"numberOfSubdomains {n_procs};\nmethod          scotch;\n"
    )


def physical_properties(req: FlowRequest) -> str:
    return (
        _hdr("dictionary", "constant", "physicalProperties")
        + f"viscosityModel  constant;\nnu              {req.fluid.kinematic_viscosity_m2_s:.9g};\n"
    )


def momentum_transport(req: FlowRequest) -> str:
    if req.turbulence == "laminar":
        body = "simulationType laminar;\n"
    else:
        body = (
            "simulationType RAS;\nRAS\n{\n    model           kOmegaSST;\n"
            "    turbulence      on;\n    printCoeffs     on;\n"
            "    viscosityModel  Newtonian;\n}\n"
        )
    return _hdr("dictionary", "constant", "momentumTransport") + body


def inlet_turbulence(req: FlowRequest) -> tuple[float, float]:
    """Inlet ``k`` and ``omega`` from intensity and length scale (kOmegaSST)."""
    assert req.turbulence_intensity is not None and req.turbulence_length_scale_m is not None
    k = 1.5 * (req.turbulence_intensity * req.inlet_velocity_m_s) ** 2
    omega = math.sqrt(k) / (_CMU_QUARTER * req.turbulence_length_scale_m)
    return k, omega


def _field(
    cls: str, obj: str, dims: str, internal: str, boundary: str, *, include_constraints: bool = True
) -> str:
    inc = '    #includeEtc "caseDicts/setConstraintTypes"\n\n' if include_constraints else ""
    return (
        _hdr(cls, "0", obj)
        + f"dimensions      {dims};\n\ninternalField   {internal};\n\n"
        + f"boundaryField\n{{\n{inc}{boundary}}}\n"
    )


def field_files(req: FlowRequest) -> dict[str, str]:
    v = req.inlet_velocity_m_s
    files = {
        "0/U": _field(
            "volVectorField",
            "U",
            "[0 1 -1 0 0 0 0]",
            "uniform (0 0 0)",
            '    ".*"\n    {\n        type            noSlip;\n    }\n\n'
            '    "inlet.*"\n    {\n        type            surfaceNormalFixedValue;\n'
            f"        refValue        uniform {-v:.9g};\n    }}\n\n"
            '    "outlet.*"\n    {\n        type            inletOutlet;\n'
            "        inletValue      uniform (0 0 0);\n        value           uniform (0 0 0);\n"
            "    }\n",
        ),
        "0/p": _field(
            "volScalarField",
            "p",
            "[0 2 -2 0 0 0 0]",
            "uniform 0",
            '    ".*"\n    {\n        type            zeroGradient;\n    }\n\n'
            '    "outlet.*"\n    {\n        type            fixedValue;\n'
            "        value           uniform 0;\n    }\n",
        ),
    }
    if req.turbulence == "kOmegaSST":
        k, omega = inlet_turbulence(req)
        files["0/k"] = _field(
            "volScalarField",
            "k",
            "[0 2 -2 0 0 0 0]",
            f"uniform {k:.9g}",
            '    ".*"\n    {\n        type            kqRWallFunction;\n'
            f"        value           uniform {k:.9g};\n    }}\n\n"
            '    "inlet.*"\n    {\n        type            fixedValue;\n'
            f"        value           uniform {k:.9g};\n    }}\n\n"
            '    "outlet.*"\n    {\n        type            inletOutlet;\n'
            f"        inletValue      uniform {k:.9g};\n        value           uniform {k:.9g};\n"
            "    }\n",
        )
        files["0/omega"] = _field(
            "volScalarField",
            "omega",
            "[0 0 -1 0 0 0 0]",
            f"uniform {omega:.9g}",
            '    ".*"\n    {\n        type            omegaWallFunction;\n'
            f"        value           uniform {omega:.9g};\n    }}\n\n"
            '    "inlet.*"\n    {\n        type            fixedValue;\n'
            f"        value           uniform {omega:.9g};\n    }}\n\n"
            '    "outlet.*"\n    {\n        type            inletOutlet;\n'
            f"        inletValue      uniform {omega:.9g};\n"
            f"        value           uniform {omega:.9g};\n    }}\n",
        )
        files["0/nut"] = _field(
            "volScalarField",
            "nut",
            "[0 2 -1 0 0 0 0]",
            "uniform 0",
            '    ".*"\n    {\n        type            nutkWallFunction;\n'
            "        value           uniform 0;\n    }\n\n"
            '    "(inlet|outlet).*"\n    {\n        type            calculated;\n'
            "        value           uniform 0;\n    }\n",
        )
    return files


def post_functions(req: FlowRequest) -> list[str]:
    """Function objects the solve writes, read back for the result."""
    funcs = [
        "#includeFunc patchAverage(patch=inlet, fields=(p U))",
        "#includeFunc patchAverage(patch=outlet, fields=(p U))",
        "#includeFunc patchDifference(patch1=inlet, patch2=outlet, fields=(p))",
        "#includeFunc patchFlowRate(patch=inlet)",
        "#includeFunc patchFlowRate(patch=outlet)",
    ]
    if req.turbulence == "kOmegaSST":
        funcs.append("#includeFunc yPlus")
    return funcs


def build_case_files(req: FlowRequest, lo: Vec3, hi: Vec3, max_cells: int) -> dict[str, Any]:
    """All text files of the case, keyed by path relative to the case directory,
    plus the plan used (so the result can report it)."""
    blo, bhi, cells, size = background_plan(lo, hi, req.mesh.cell_size_m)
    s = req.scale
    inside_m = (req.inside_point[0] * s, req.inside_point[1] * s, req.inside_point[2] * s)
    files = {
        "system/blockMeshDict": block_mesh_dict(blo, bhi, cells),
        "system/snappyHexMeshDict": snappy_dict(req, inside_m, max_cells),
        "system/controlDict": control_dict(req, post_functions(req)),
        "system/fvSchemes": fv_schemes(),
        "system/fvSolution": fv_solution(req),
        "constant/physicalProperties": physical_properties(req),
        "constant/momentumTransport": momentum_transport(req),
        **field_files(req),
    }
    if req.solver.n_procs > 1:
        files["system/decomposeParDict"] = decompose_par_dict(req.solver.n_procs)
    return {
        "files": files,
        "plan": {
            "background_cells": list(cells),
            "background_cell_size_m": size,
            "cell_size_defaulted": req.mesh.cell_size_m is None,
            "surface_refinement": [
                req.mesh.surface_refinement_min,
                req.mesh.surface_refinement_max,
            ],
            "inside_point_m": list(inside_m),
        },
    }
