# Tier-1 — `cadquery.*` happy path (HP-CAD)

Validates: MET-414 (sub-deliverable 5/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario cad-hp`

Ten happy-path scenarios for the cadquery MCP tool group from a
Claude-as-real-user perspective.

> **Note:** The local CI image does not always ship CadQuery's
> CAD kernel. Each scenario's pre-flight checks the manifest is
> registered first; if the handler call fails with a "cadquery
> not installed" error the scenario reports BLOCKED, not FAIL —
> that's an environment issue, not an acceptance gap.

---

## Scenario: HP-CAD-01 — simple parametric box (50×30×10mm)
Validates: MET-337, MET-340
Tier: 1

### Given
- `cadquery.create_parametric` is in `tool/list`.

### When
1. Call `cadquery.create_parametric` with `shape_type="box"`,
   `parameters={width:50, length:30, height:10}`,
   `output_path="/tmp/uat-hp-cad-box.step"`.

### Then
- Returns `status="success"` with `cad_file`, `volume_mm3`,
  `surface_area_mm2`.
- `volume_mm3` ≈ 15000 (within 1% — BRep volume can drift).
- The STEP file on disk parses as valid OCCT structure.

---

## Scenario: HP-CAD-02 — STEP → GLB conversion
Validates: MET-340 (export pipeline)
Tier: 1

### Given
- The STEP from HP-CAD-01.

### When
1. Call `cadquery.export_geometry` with `format="glb"` (or the
   tool-specific equivalent that triggers OCCT → glTF).

### Then
- A `.glb` file is produced and reachable.
- The mesh-to-node manifest is emitted (mapping triangles ↔
  source faces) with at least one entry per BRep face.

---

## Scenario: HP-CAD-03 — parametric change regenerates STEP
Validates: MET-340 (re-generation), twin-core SUPERSEDES
Tier: 1

### Given
- A DesignElement node already linked to the HP-CAD-01 STEP.

### When
1. Call `cadquery.create_parametric` again with new dimensions
   `{width:60, length:40, height:15}` for the same DesignElement.

### Then
- A new STEP is produced.
- The old STEP/DesignElement version is preserved via a
  `SUPERSEDES` edge (queryable via `twin.thread_for`).

---

## Scenario: HP-CAD-04 — multi-feature part (box + through-hole)
Validates: MET-337 (boolean_operation)
Tier: 1

### Given
- A box part and a cylindrical cutter aligned to its center.

### When
1. Call `cadquery.boolean_operation` with `operation="cut"`
   passing the box and cylinder.

### Then
- The resulting STEP has a hole in the expected location.
- Computed volume equals box volume minus cylinder volume
  (within 2%).

---

## Scenario: HP-CAD-05 — assembly of two parts
Validates: MET-337 (create_assembly)
Tier: 1

### Given
- Two parts: A with a 5mm hole, B with a 5mm pin.

### When
1. Call `cadquery.create_assembly` mating the pin into the hole
   with axial alignment.

### Then
- The assembly STEP preserves the mating constraint.
- Bounding box is consistent with the assembled geometry.

---

## Scenario: HP-CAD-06 — material properties attached
Validates: MET-340 (material metadata)
Tier: 1

### Given
- A box part from HP-CAD-01.

### When
1. Either via the cadquery tool or via the resulting Twin node
   update, set `material="aluminum_6061"`.

### Then
- The DesignElement node carries `material="aluminum_6061"` and
  associated mechanical property fields (E, ρ, ν).

---

## Scenario: HP-CAD-07 — combined STEP + GLB export
Validates: MET-340 (export_geometry)
Tier: 1

### Given
- A part already produced.

### When
1. Call `cadquery.export_geometry` with `formats=["step","glb"]`.

### Then
- Both files land in MinIO (or the local artifact store).
- The response returns reachable URLs (or paths) for both
  formats.

---

## Scenario: HP-CAD-08 — long-running operation emits progress
Validates: MET-340 + MET-388 (streaming progress)
Tier: 1

### Given
- A complex part request that takes more than 1 second to
  generate (e.g. an assembly with ≥ 100 features).

### When
1. Call `cadquery.create_parametric` over an MCP transport that
   supports notifications.

### Then
- At least one MCP `progress` notification fires before the
  final response.
- Each notification carries a monotonic progress fraction
  ∈ [0, 1].

---

## Scenario: HP-CAD-09 — result links to MinIO blob via WorkProductMapping
Validates: MET-340 (artifact wiring)
Tier: 1

### Given
- A part created via HP-CAD-01.

### When
1. Inspect the resulting DesignElement node in the twin.

### Then
- `workProductId` resolves through `WorkProductMapping` to the
  STEP blob URL in MinIO.
- The URL is fetchable (HEAD returns 200 in the dev env).

---

## Scenario: HP-CAD-10 — CAD result + Twin node round-trip
Validates: MET-340 + MET-382
Tier: 1

### Given
- A part generated via `cadquery.create_parametric`.

### When
1. Capture the generation parameters.
2. Call `twin.get_node` on the resulting DesignElement.

### Then
- Properties on the Twin node match the generation parameters
  (shape_type, dimensions, material if set, output paths).

---

## Acceptance

- All 10 scenarios PASS.
- Report committed.
