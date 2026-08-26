# High-Fidelity Simulation

How MetaForge turns a meshed solid into a CalculiX result that can be trusted in
a design review — what the solver is actually given, which units it is given it
in, and how the numbers are checked against closed-form solutions.

## The problem: a mesh is not an analysis

Meshers in the pipeline (FreeCAD, gmsh, Netgen) emit **geometry-only** decks:
`*NODE` and `*ELEMENT` blocks, sometimes a named set, and nothing else.
CalculiX needs considerably more before it will produce a result:

| Card | Supplies | Without it |
|------|----------|------------|
| `*MATERIAL` / `*ELASTIC` | Stiffness | No stiffness matrix |
| `*SOLID SECTION` | Binds material to elements | Elements contribute nothing |
| `*BOUNDARY` | Restraint | Singular matrix (rigid body modes) |
| `*CLOAD` / `*DLOAD` | Loads | Zero stress everywhere |
| `*STATIC` / `*FREQUENCY` / `*HEAT TRANSFER` | The analysis itself | No step to run |
| `*NODE FILE` / `*EL FILE` | Output requests | **Solver exits 0 with an empty `.frd`** |

The last row is the dangerous one. A deck missing its output requests solves
successfully and writes a result file containing only the mesh, which is
indistinguishable from a model that genuinely has no stress in it.

`tool_registry/tools/calculix/deck.py` closes this gap: it takes a parsed mesh
plus a load case and emits a complete, solvable deck. The mesh is pulled in with
`*INCLUDE` rather than copied, so a million-element mesh is not re-serialised
once per load case in a sweep.

## Units are a fidelity concern

CalculiX is unit-agnostic — it solves whatever numbers the deck contains. A
density in kg/m³ sitting next to a modulus in MPa produces stresses wrong by
twelve orders of magnitude, with no error anywhere.

Everything in `materials.py` is stored in the **N-mm-s-tonne-K** consistent
system, the standard companion to a millimetre mesh:

| Quantity | Unit | From SI |
|----------|------|---------|
| Length | mm | m × 10³ |
| Force | N | — |
| Stress / modulus | MPa (N/mm²) | Pa × 10⁻⁶ |
| Density | tonne/mm³ | kg/m³ × 10⁻¹² |
| Energy | mJ (N·mm) | J × 10³ |
| Power | mW | W × 10³ |
| Conductivity | mW/(mm·K) | W/(m·K) × 1 *(identity)* |
| Specific heat | mJ/(tonne·K) | J/(kg·K) × 10⁶ |

Stresses recovered from a deck written this way come out in MPa, which is what
the Twin, the constraint engine, and every safety factor assume.

Datasheet values are entered in familiar SI units and converted once, at library
definition time, so no call site has to remember the factors.

## Defining an analysis

A load case describes the physics. Regions are selected either by a named set
the mesher emitted, or geometrically by bounding-box face — meshers rarely emit
named face sets, so the geometric selector is what most cases need.

```json
{
  "mesh_file": "bracket.inp",
  "analysis_type": "static_stress",
  "material": "al6061_t6",
  "load_cases": [
    {
      "name": "hard_landing",
      "constraints": [{"region": {"face": "zmin"}, "kind": "fixed"}],
      "point_loads": [{"region": {"face": "zmax"}, "fz": -250.0}],
      "gravity_mm_s2": [0.0, 0.0, -98100.0]
    }
  ]
}
```

Each case is solved as its own deck; the reported safety factor is the **worst
across every case**, and `governing_load_case` names the one that produced it.

Supported inputs:

- **Constraints** — `fixed`, `pinned`, `roller_x/y/z`, `symmetry_x/y/z`, or
  explicit degrees of freedom.
- **Point loads** — forces in N, split across the region by default (a *total*
  force) or applied in full at every node.
- **Pressures** — face pressure in MPa on an element set.
- **Gravity / body acceleration** — vector in mm/s², emitted as `*DLOAD GRAV`.
- **Thermal** — prescribed temperatures (K), heat fluxes (mW), and convective
  film conditions.

Analysis types map to real CalculiX steps: `static_stress` → `*STATIC`
(optionally `NLGEOM`), `modal` → `*FREQUENCY`, and thermal →
`*HEAT TRANSFER`, steady-state or transient.

### Cases that cannot produce a meaningful result are rejected

Validation runs before any file is written, so the error names the load case
rather than surfacing later as a solver convergence failure:

- A static or modal case with **no constraints** — an unrestrained model has
  rigid body modes and yields a singular stiffness matrix.
- A static case with **no load** — the answer is zero stress everywhere.
- A thermal case with **no thermal boundary conditions**.
- A region that **selects no nodes** — the load would silently do nothing.
- An **unknown material** — substituting a default would quietly change the
  physics, so it is fatal rather than defaulted.

## Safety factors

Safety factor is derived from the resolved material's yield strength and the
governing von Mises stress:

```
safety_factor = yield_strength_mpa / max_von_mises_mpa
```

An unloaded region has infinite margin rather than a division error. Callers
needing a certified or vendor-specific value can pass `material_overrides`
without adding a library entry.

## Mesh quality

Mesh quality is the dominant error source in a linear FEA result. A sliver
tetrahedron — four nearly-coplanar nodes — has a near-singular Jacobian and can
report stresses wrong by an order of magnitude while the solver still converges
and exits zero. Counting nodes and elements cannot detect that.

`calculix.validate_mesh` measures every solid element's actual geometry:

| Metric | Meaning | Good value |
|--------|---------|------------|
| `aspect_ratio` | Longest edge ÷ shortest edge | 1.0; unbounded as it degenerates |
| `min_angle` | Smallest dihedral angle (tets), degrees | 70.53° for a regular tet |
| `min_scaled_jacobian` | Corner Jacobian normalised by edge lengths | 1.0; ≤ 0 is degenerate |
| `volume` | Signed element volume | Positive; negative means inverted |
| `avg_quality` | Worse of shape score and Jacobian, in [0, 1] | Near 1.0 |

The scaled Jacobian samples **every** corner, not just the first — a
right-angled corner tetrahedron has one orthogonal frame and three worse ones,
and it is the worst corner that governs behaviour.

Aspect ratio and scaled Jacobian are complementary and both are reported: a
uniformly squashed hexahedron is still rectangular, so its Jacobian stays 1.0
while its aspect ratio rises. Reporting only one would miss half the bad meshes.

Dihedral angles are defined for tetrahedra. A hexahedral mesh reports
`min_angle: null` — *not measured* — rather than `0`, which would read as fully
degenerate.

The response also lists the worst individual elements by id, so a caller can
refine them locally instead of re-meshing from scratch.

## Verification against closed-form solutions

`tests/integration/test_calculix_solver_fidelity.py` meshes a 100 × 10 × 10 mm
Al6061-T6 cantilever, generates decks, runs the real `ccx` binary, and compares
against analytical results:

| Analysis | Closed form | Analytical | Computed | Error |
|----------|-------------|-----------|----------|-------|
| Tip deflection | `PL³/(3EI)` | 0.5806 mm | 0.5800 mm | 0.1% |
| Bending stress | `Mc/I` | 60.00 MPa | 61.57 MPa | +2.6% |
| First bending mode | `(βL)²/2π · √(EI/ρAL⁴)` | 816.03 Hz | 815.91 Hz | 0.015% |
| 1-D conduction | `ΔT = QL/(kA)` | 352.99 K | 353.08 K | 0.02% |

The stress tolerance is deliberately looser. Beam theory assumes a St Venant
distribution and ignores the stress concentration at a fully built-in root, so a
*correct* FEA result is expected to sit slightly above `Mc/I`. The test asserts
that direction explicitly — a result matching `Mc/I` exactly would suggest the
root constraint never got applied.

The modal check also asserts that the first two modes coincide: bending about Y
and Z is identical for a square section, so a degenerate pair is evidence the
mass matrix is real.

These tests skip when `ccx` is not installed.

## Reading modal results

A `*FREQUENCY` step writes mode shapes to the `.frd` but eigenvalues only to the
`.dat`. Reading the `.frd` alone reports a successful modal analysis with no
frequencies in it, so `parse_dat_frequencies` reads the eigenvalue block and
returns the cyclic frequency in Hz alongside the eigenvalue and rad/s.

## Authored decks still work

A file that already carries its own `*STEP` is solved exactly as authored — a
hand-written deck is respected, not regenerated. A geometry-only mesh arriving
with no load cases logs a warning naming the argument that fixes it, rather than
silently reporting zero stress.

## Current limitations

- **Linear elastic only.** No plasticity, creep, or contact. Safety factors
  above yield are extrapolations, not predictions of behaviour.
- **`*FILM` convection needs a named element set** and a CalculiX face label;
  film conditions cannot yet be derived from a bounding-box face.
- **Material properties are room-temperature handbook values** for the stated
  temper or grade — design-review inputs, not certifications.
- **Contact between parts is not modelled**; assemblies solve as separate
  bodies unless the mesh already ties them.
