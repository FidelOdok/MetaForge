# Tier-1 — `calculix.*` happy path (HP-FEA)

Validates: MET-415 (sub-deliverable 6/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario fea-hp`

Ten happy-path scenarios for the calculix MCP tool group from a
Claude-as-real-user perspective.

> **Note:** The dev image runs `ccx` inside the calculix
> container. If `ccx` is not reachable, scenarios that need a
> solver run report BLOCKED rather than FAIL.

---

## Scenario: HP-FEA-01 — simple cantilever beam stress
Validates: MET-340 (calculix routing), MET-379/380 (entrypoint)
Tier: 1

### Given
- A 100×10×5mm beam STEP file.

### When
1. Call `calculix.solve` with `mesh_size=2mm`, fixed boundary on
   one end face, 10N point load on the opposite end face,
   material `aluminum_6061`.

### Then
- A `.frd` output is produced.
- Max von-Mises stress is non-zero and within an order of
  magnitude of the analytical estimate (σ = 6FL/bh² ≈ 24 MPa
  for the given geometry).

---

## Scenario: HP-FEA-02 — mesh generation from STEP input
Validates: calculix mesh tool
Tier: 1

### Given
- A small STEP fixture.

### When
1. Call the meshing tool (`calculix.mesh` or equivalent) with
   `element_size=1mm`.

### Then
- A mesh file (`.unv` / `.inp`) is produced.
- Element count grows roughly cubically with 1/element_size when
  rerun at coarser sizes (sanity check, not a strict bound).

---

## Scenario: HP-FEA-03 — stress results output as `.frd`
Validates: calculix output format
Tier: 1

### Given
- A completed solve from HP-FEA-01.

### When
1. Inspect the result file.

### Then
- The file is `.frd` format.
- It is parseable by CalculiX `cgx` (or the local Python parser).

---

## Scenario: HP-FEA-04 — top-stress element identification
Validates: calculix result post-processing
Tier: 1

### Given
- A completed `.frd` from HP-FEA-01.

### When
1. Call the top-stress query tool (or the post-process script).

### Then
- The returned element ID and stress tensor match the maximum in
  the `.frd` (max stress location is reproducibly identified).

---

## Scenario: HP-FEA-05 — convergence within tolerance
Validates: solver convergence sanity
Tier: 1

### Given
- The HP-FEA-01 model.

### When
1. Re-run with progressively finer mesh (0.5mm, then 0.25mm).
2. Compare top-element stress across the three runs.

### Then
- Stress at the corresponding location converges within 5%
  between the two finest meshes (Richardson extrapolation
  within a tolerance band).

---

## Scenario: HP-FEA-06 — material assignment
Validates: material library wiring
Tier: 1

### Given
- The HP-FEA-01 model.

### When
1. Solve with `material="aluminum_6061"` (E=68.9 GPa, ν=0.33,
   ρ=2700 kg/m³).

### Then
- The solver receives those E / ν / ρ values (verifiable from
  the generated `.inp` deck or the response metadata).
- Resulting displacements are consistent with aluminum's stiffness.

---

## Scenario: HP-FEA-07 — boundary conditions applied correctly
Validates: BC plumbing
Tier: 1

### Given
- The HP-FEA-01 model with a fixed bottom face and 10N applied
  to the top face.

### When
1. Inspect the reaction forces at the fixed face after solve.

### Then
- The summed reaction force on the fixed face equals -10N
  (within solver tolerance, < 1% mismatch).

---

## Scenario: HP-FEA-08 — result links back to SimulationRun graph node
Validates: calculix → twin wiring
Tier: 1

### Given
- A completed solve.

### When
1. Look up the resulting `SimulationRun` node in the twin.

### Then
- The node carries `model_id`, `device_id`, and an
  `accuracy_metric` (residual norm, max stress, or equivalent).

---

## Scenario: HP-FEA-09 — performance bound
Validates: calculix throughput
Tier: 1

### Given
- A 10K-element mesh.

### When
1. Run the full mesh + solve pipeline once.

### Then
- Wall-clock under 30 seconds on standard dev hardware.
  Hardware-constrained dev rigs may report BLOCKED.

---

## Scenario: HP-FEA-10 — long-running emits MCP progress notifications
Validates: MET-388
Tier: 1

### Given
- A solve that takes ≥ 5 seconds.

### When
1. Run via an MCP transport that supports notifications.

### Then
- At least two `progress` notifications fire before the final
  response.
- Each notification carries a monotonic progress fraction.

---

## Acceptance

- All 10 scenarios PASS.
- Report committed.
