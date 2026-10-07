# run_fea

Run a finite element analysis with CalculiX: linear static stress or modal (natural frequencies).

## What it does

1. Takes a volume mesh and the case `calculix.run_fea` needs: material, fixed face and, for static stress, the loaded face and force vector
2. Invokes `calculix.run_fea` with exactly those arguments (FORGE-561: it used to send arguments the tool does not take, so every call failed)
3. Returns peak von Mises stress and displacement, or modal frequencies, and the `.frd` path; it records nothing itself

## Tools Required

- `calculix.run_fea` -- CalculiX static stress or modal solve

## Input

- `work_product_id` -- twin work_product id for the mechanical design
- `mesh_file` -- volume mesh (.inp), e.g. from `generate_mesh`
- `load_case` -- name of the load case
- `analysis_type` -- `static_stress` (default; `static` is accepted) or `modal`
- `material` -- `{"name": ...}` or explicit `youngs_modulus_mpa` / `poissons_ratio`; modal needs the name
- `fixed_node_set` -- the face held fixed
- `load_node_set`, `load_force_n` (`[Fx, Fy, Fz]`, N) -- required for static stress
- `num_modes` -- modal only (default 3)
- `yield_strength_mpa` -- optional, cited; gives a safety factor

## Output

- `max_stress_mpa`, `max_displacement_mm` (static), `frequencies_hz` (modal)
- `safety_factor` -- `yield_strength_mpa / max_stress_mpa`, or null when no yield was given (the tool has no yield data)
- `frd_path`, `solver_time_s`

## Limitations

- Linear static and modal only; no thermal (see `run_cfd` / `calculix.run_thermal`), contact or nonlinear material
- A solve that reports no stress (or no frequencies) fails validation rather than reading as zero
- Result quality depends on the mesh; meshing is `generate_mesh`
