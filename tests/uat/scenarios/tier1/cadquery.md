# Tier-1 — `cadquery.*` scenarios

Validates the CAD generation MCP surface from a Claude-as-real-user
perspective. Run on Cycle gates.

Every scenario assumes the MetaForge MCP server is connected and
the seven `cadquery.*` tools are listed in `tool/list`.

> **Note:** The local CI image does not always ship CadQuery's
> CAD kernel. Each scenario's pre-flight checks the manifest is
> registered first; if the handler call fails with a "cadquery not
> installed" error, the scenario reports BLOCKED (not FAIL) — that's
> an environment issue, not an acceptance gap.

---

## Scenario: create a parametric box and inspect properties
Validates: MET-337 (manifest), MET-340 (E2E call routing)
Tier: 1

### Given
- `cadquery.create_parametric` and `cadquery.get_properties` both
  appear in `tool/list`.

### When
1. Call `cadquery.create_parametric` with:
   - `shape_type`: `"box"`
   - `parameters`: `{ "width": 50, "length": 30, "height": 10 }`
   - `output_path`: `"/tmp/uat-tier1-box.step"`
2. Call `cadquery.get_properties` on the resulting `cad_file`.

### Then
- Step 1 returns `status: "success"` with a `cad_file` path,
  `volume_mm3`, and `surface_area_mm2`.
- Step 1's `volume_mm3` is approximately 50 × 30 × 10 = 15000
  (within 1% — CadQuery's BRep volume can drift slightly).
- Step 2 returns properties consistent with step 1's output.

---

## Scenario: invalid shape_type returns a clean tool error
Validates: MET-340 error-handling contract
Tier: 1

### Given
- `cadquery.create_parametric`'s manifest declares `shape_type` as
  one of {box, cylinder, sphere, cone, bracket, plate, enclosure}.

### When
1. Call `cadquery.create_parametric` with `shape_type: "tetrahedron"`,
   any parameters, any output_path.

### Then
- The response is a tool error (`-32001`) OR `status: "failure"`.
- The error message names the rejected shape_type.
- The MCP transport itself does NOT crash — the next tool call still
  succeeds.

---

## Scenario: create a cylinder
Validates: MET-337 (manifest coverage)
Tier: 1

### Given
- `cadquery.create_parametric` appears in `tool/list`.

### When
1. Call `cadquery.create_parametric` with:
   - `shape_type`: `"cylinder"`
   - `parameters`: `{ "radius": 25, "height": 50 }`
   - `output_path`: `"/tmp/uat-tier1-cylinder.step"`

### Then
- Returns `status: "success"`.
- `volume_mm3` is approximately π × 25² × 50 ≈ 98174 (within 2%).

---

## Scenario: bounding box reports correct dimensions
Validates: MET-340 response-schema contract
Tier: 1

### Given
- `cadquery.create_parametric` reachable.

### When
1. Create a box with `{ "width": 100, "length": 60, "height": 20 }`.

### Then
- The response includes a `bounding_box` object.
- The bounding-box dimensions match the requested
  width/length/height (within 0.1mm of literal).

---

## Scenario: missing required parameter is caught
Validates: MET-340 schema enforcement
Tier: 1

### Given
- `cadquery.create_parametric`'s schema requires `output_path`.

### When
1. Call it with `shape_type: "box"`, parameters, and **no**
   `output_path`.

### Then
- The response is a tool error.
- The error message references the missing field
  (`output_path`, `required`, or `parameters_used` is informative).
- The MCP server stays alive (subsequent calls work).

---

## Scenario: tool/list reports all seven cadquery tools
Validates: MET-337
Tier: 1

### Given
- (preflight only)

### When
1. (no MCP call needed beyond the agent's own discovery)

### Then
- The `cadquery.*` tools visible to the agent are exactly:
  `create_parametric`, `boolean_operation`, `get_properties`,
  `export_geometry`, `execute_script`, `create_assembly`,
  `generate_enclosure`. (Order doesn't matter.)
