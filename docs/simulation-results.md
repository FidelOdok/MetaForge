# Simulation results in 3D

A CalculiX run now keeps its result **field**, not just its peak numbers.
`calculix.run_fea` and `calculix.run_thermal` read the solved `.frd` file,
extract the outer surface of the mesh with per-vertex von Mises stress,
displacement (as a vector) or temperature, and return it as a compact
payload. `twin.record_document` stores that payload in MinIO on the
`simulation_result` work product, and the dashboard draws it as a colour
map on the mesh (FORGE-532).

Results recorded before this existed still show their numbers. They have
no field, and every view says so with a *Field not stored* note rather
than inventing a contour from the summary.

## What the solver returns

Every successful `run_fea` (static or modal) and `run_thermal` result now
carries a `field` object beside the existing summary:

| Key | Meaning |
|---|---|
| `file` | Path of `<mesh>_solved_field.json.gz` on the shared adapter workspace. Pass this to `twin.record_document` as `field_file`. |
| `base64` | The same bytes inline, for programmatic callers. A model should not copy it between tool calls (it is truncated out of chat context; see MET-684 for why carrying blobs by hand is unsafe). |
| `format`, `media_type`, `encoding` | `metaforge.sim_field/1`, `application/vnd.metaforge.sim-field+json`, `gzip`. |
| `quantities`, `ranges` | Which fields it holds (`von_mises`, `displacement_magnitude`, `temperature`) and their full-field min/max. |
| `vertex_count`, `triangle_count`, `decimated`, `source_triangle_count` | Size of the drawn surface, and whether it was simplified. |
| `error` | Only present when the field could not be built. The numeric summary is still valid; the solve never fails because of the field. |

### The payload

The file is gzipped JSON, schema `metaforge.sim_field` version 1:

```json
{
  "format": "metaforge.sim_field", "version": 1,
  "analysis_type": "static_stress",
  "units": {"length": "mm", "stress": "MPa", "displacement": "mm", "temperature": "C"},
  "positions": [x0, y0, z0, x1, ...],
  "indices": [a0, b0, c0, ...],
  "displacement": [dx0, dy0, dz0, ...],
  "fields": {
    "von_mises": {"label": "Von Mises stress", "unit": "MPa", "values": [...],
                  "min": 0.0, "max": 182.4, "peak": {"position": [...], "value": 182.4}}
  },
  "markers": [
    {"kind": "fixture", "label": "Surface1", "position": [...], "bbox": {...}},
    {"kind": "load", "label": "Surface7", "position": [...], "vector": [0, 0, -500], "unit": "N"}
  ],
  "bbox": {"min": [...], "max": [...]},
  "decimation": {"applied": false, "source_triangle_count": 18420, "cell_size_mm": null}
}
```

How it is built (`tool_registry/tools/calculix/field_payload.py`):

- **Mesh.** Nodes come from the `.frd` `2C` block and elements from its `3C`
  block (falling back to the `.inp` mesh when a file has no `3C` block). A
  face of a volume element is on the outer surface when no other element
  shares it. Second-order elements (C3D10, C3D20) contribute their corner
  nodes, so the surface is drawn as linear facets.
- **Fields.** Displacement keeps all three components. Von Mises is computed
  from the six stress components (ccx writes components, not the
  equivalent). Thermal runs carry `temperature` from the `NDTEMP` block.
  Static and thermal runs use the last result block of each kind; a modal
  run uses the first `DISP` block, which is the mode shape of mode 1.
- **Markers.** The fixture (or thermal sink) and the load (or heat source)
  are located on the mesh by the same node sets the deck was built from,
  so the viewer can draw supports and the load arrow without guessing.
- **Size.** The payload is capped at 1.5 MB compressed. Over the cap the
  surface is simplified by vertex clustering on a coarsening grid until it
  fits, and the payload says so. Field min, max and peak always come from
  the full nodal field before simplification, so the legend and the peak
  report the real maximum.

Why gzipped JSON rather than glTF: the viewer needs several selectable
per-vertex scalars, a displacement vector and markers that are not geometry.
glTF can carry custom attributes, but the adapter has no glTF writer and the
markers would end up in `extras` anyway. Gzipped JSON is standard library on
both ends, and the gateway serves it with `Content-Encoding: gzip`, so the
browser inflates it for free. Values are rounded to four significant digits
(positions to the part's own scale), which compresses to about 25 bytes per
surface vertex with stress and displacement (measured: a 7,922-vertex,
15,840-triangle surface is 195 KB). The 1.5 MB cap therefore holds about
60,000 surface vertices before any simplification.

## Recording it

Pass the field by reference when recording the result:

```json
{
  "tool": "twin.record_document",
  "arguments": {
    "name": "Bracket static 500N",
    "document_type": "simulation_result",
    "content": "{\"max_von_mises_mpa\": 182.4, \"max_displacement_mm\": 0.41}",
    "metadata": {"max_von_mises_mpa": 182.4, "max_displacement_mm": 0.41, "load_case": "tip-500N"},
    "field_file": "/workspace/bracket_solved_field.json.gz",
    "analysed_geometry_node_id": "<cad_model node id>",
    "load_case_spec": {"material": {"name": "aluminum_6061"}, "fixed_node_set": "Surface1",
                       "load_node_set": "Surface7", "load_force_n": [0, 0, -500]}
  }
}
```

New `twin.record_document` arguments, all optional and valid only for
`document_type="simulation_result"`:

- `field_file` (preferred) or `field_base64`: the payload. A relative path
  resolves against the adapter workspace, as `twin.commit_geometry`'s
  `file_path` does. Anything that is not a `metaforge.sim_field` payload is
  rejected before the result is created.
- `analysed_geometry_node_id` (and optionally `analysed_geometry_revision`):
  the `cad_model` that was meshed. It is pinned with its revision and content
  hash, and becomes a `DERIVES_FROM` edge, so a later geometry revision can
  mark the result stale (FORGE-527).
- `load_case_spec` and `fixtures`: the boundary conditions actually solved.
  When `fixtures` is omitted it is taken from the payload's own markers.

The field is stored as a second blob on the same node,
`work-products/<node_id>/<name>.field.json.gz`. The summary JSON stays the
node's primary file. The node's metadata gains:

| Key | Meaning |
|---|---|
| `field_stored` | `true` when the blob reached MinIO. |
| `field_object_key`, `field_content_hash`, `field_size_bytes` | Where it is, its SHA-256 and size. |
| `field_format`, `field_analysis_type`, `field_quantities`, `field_ranges` | What it holds. |
| `field_store_error` | Why the upload failed, when it did. The result is still recorded. |
| `analysed_geometry` | `{node_id, revision, name, content_hash}` of the analysed geometry. |
| `load_case_spec`, `fixtures`, `loads` | Boundary conditions. |
| `mesh_convergence` | Optional: the `calculix.check_mesh_convergence` output for the sweep this run finished. |

## Serving it

- `GET /v1/simulation/results` lists each result with `hasField`,
  `analysisType`, `fieldQuantities`, `fieldRanges`, `analysedGeometry`,
  `loadCaseSpec`, `fixtures`, `loads` and `meshConvergence`, beside the
  existing numbers.
- `GET /v1/simulation/results/{id}/field` serves the payload with
  `Content-Type: application/vnd.metaforge.sim-field+json`,
  `Content-Encoding: gzip` and an `ETag` of the field's content hash. It
  answers **404 `field not stored`** for a result without a field, which the
  dashboard treats as a normal state.
- `GET /v1/twin/nodes/{id}/file` also knows the solver formats now (`frd` and
  `dat` as text, `vtu` as XML).

See the [gateway API reference](reference/gateway-api.md) for the full
schemas.

## On the dashboard

- **`/sim`.** Opening a result (click its name; the newest opens by default)
  shows the field viewer: a colour map of von Mises stress, displacement
  magnitude or temperature, a legend with the full-field min, max and peak,
  the deformed shape under a scale-factor slider (it starts at a scale that
  makes the largest deflection about 5% of the part size), fixture and load
  markers, and a hover probe of the value under the cursor. Selecting two
  results shows the numeric compare and a side-by-side 3D compare with one
  camera, one quantity and one colour scale spanning both, so the same colour
  means the same value on each side. A result with `meshConvergence` also
  shows the convergence chart: peak von Mises against element count (or
  element size when counts were not recorded) with the converged verdict.
- **`/twin`.** A selected `simulation_result` node's FEA panel includes the
  same viewer under its numbers.

The colour map is perceptually uniform and sequential (the "plasma" ramp):
brightness rises with the value, so regions can be ranked in greyscale or by
a colour-blind reader, which a rainbow map does not allow.

## Mesh convergence

`calculix.check_mesh_convergence` accepts an optional `element_count` per
point. Record its output on the finest run's result as
`metadata.mesh_convergence` so the Sim page can chart it:

```json
{"points": [{"element_size_mm": 4, "max_von_mises_mpa": 171.0, "element_count": 3400},
            {"element_size_mm": 2, "max_von_mises_mpa": 182.4, "element_count": 21800}],
 "changes": [{"from_element_size_mm": 4, "to_element_size_mm": 2, "percent_change": 6.67}],
 "converged": false,
 "recommendation": "Not converged ..."}
```

## Observability

| Signal | Meaning |
|---|---|
| `metaforge_sim_field_payload_total{analysis_type, outcome}` | Field builds per solve: `built`, `decimated` or `failed`. |
| `metaforge_sim_field_payload_bytes{analysis_type}` | Compressed payload size. |
| `metaforge_sim_field_store_total{outcome}` | Recorder uploads: `stored`, `failed` or `invalid`. |
| `SimFieldPayloadFailures`, `SimFieldStoreFailures` | Alerts (`observability/alerting/rules.yaml`) when either fails more than 3 times in 30 minutes. Both failures degrade silently to the numbers-only view, so the counters are the only signal. |

Logs: `calculix_field_payload_built` and `calculix_field_payload_failed`
(adapter), `sim_field_blob_store_failed` (recorder),
`simulation_field_served` (gateway). Spans: `calculix.result_field`,
`calculix.build_field_payload`, `simulation.get_result_field`.
