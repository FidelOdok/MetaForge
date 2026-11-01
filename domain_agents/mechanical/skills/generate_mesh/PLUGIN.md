---
description: Turn a committed CAD part into a CalculiX-ready tetrahedral finite element mesh with gmsh, check its quality, and identify the fixed and loaded faces by their real coordinates. Use when the user asks to mesh a part, prepare a part for FEA or thermal analysis, refine or coarsen a mesh, or find which mesh surface group is a given face (for example "the face at x = 0"), and before any calculix.run_fea or calculix.run_thermal call that has no mesh yet.
---

# generate_mesh

Produce the volumetric mesh a structural or thermal solve runs on, from the
exact geometry revision in the twin, and leave the caller knowing which mesh
surface group is which physical face. A mesh is only useful if its face
groups are identified correctly: picking the wrong one has, in this project,
produced an FEA result ten times too stiff with no error.

## When to use it

- "Mesh the bracket for FEA at about 2 mm."
- "Which surface group is the wall-mount face?"
- "The stress has not converged; give me a finer mesh / second-order elements."
- As the meshing step inside `run_fea`, `validate_stress` or a thermal run.

Not for:

- Running the analysis itself: `run_fea` (stress, deflection, modal),
  `validate_stress` (pass/fail against an allowable stress) or
  `calculix.run_thermal`.
- Surface meshes for printing or rendering: export STL from the CAD tools
  (`cadquery.export_geometry`), not a FE mesh.
- A part with no committed geometry: design and commit it first
  (`generate_cad_script`, `generate_cad`).

## Tools and profile

| Step | Tool | Profile that serves it |
|---|---|---|
| Find the part | `twin.find_by_property`, `twin.get_node` | every profile |
| Get its CAD file | `twin.stage_work_product_file` | `mechanical`, `simulation`, `robotics` |
| Mesh it | `freecad.generate_mesh` | `mechanical`, `simulation` |
| Re-read the face table later | `freecad.list_named_faces` | `mechanical`, `simulation` |
| Check mesh quality | `calculix.validate_mesh` | `simulation` |
| Part dimensions for sizing | `cadquery.get_properties` | `mechanical`, `simulation` |

Use the **`simulation`** profile: it serves every step, including the quality
check. On `mechanical` you can mesh but cannot run `calculix.validate_mesh`;
say so rather than skipping the check.

## Inputs you need before you start

Ask the user for anything missing.

| Input | Why it matters | Example |
|---|---|---|
| The part | Its twin node id or name; the mesh is valid only for that revision | `Upper Arm Link` |
| What the mesh is for | Static stress, modal and thermal all mesh the same way, but the faces you need to identify differ | "static stress, 150 N tip load" |
| Where it is held and loaded, in words | You will map these to surface groups by coordinates | "fixed at the two bolt holes at x = 0, load on the tip face" |
| Target element size, if the user has one | Otherwise derive it from the geometry (step 2) and say how | 2 mm |
| Element order, if the user cares | Second order is far more accurate in bending | 2 |

## Procedure

### 1. Stage the exact geometry

1. Find the part's CAD work product: `metaforge://twin/brief/<project_id>`,
   `twin.find_by_property`, or `twin.get_node`. Note its node id and revision.
2. Call `twin.stage_work_product_file` with that `node_id`. Use the returned
   `file_path` as the mesher input. Do this even for a part you just
   authored: `freecad.generate_mesh` has no twin access and does not accept a
   work product id, and a session export path goes stale.

### 2. Choose the element size

Call `cadquery.get_properties` on the staged file if you do not know the
part's dimensions. Start at roughly a tenth of the smallest dimension that
matters structurally (wall thickness, web, the section under load), and make
sure at least two elements span that thickness. State the number you chose
and why. If the user gave a size, use theirs.

### 3. Mesh

Call `freecad.generate_mesh` with:

- `input_file`: the staged `file_path` (a STEP file)
- `output_format`: `"inp"`. Only .inp gives node counts, the face table and
  a file CalculiX can solve; `unv` and `stl` return zero counts and no faces.
- `element_size`: from step 2
- `element_order`: `1` (C3D4, linear) by default; `2` (C3D10, quadratic) for
  bending-dominated parts or when convergence is slow. Prefer raising the
  order before shrinking the size.
- `algorithm`: leave it out or pass `"gmsh"`. Only gmsh exists in the
  adapter; `netgen` and `mefisto` are accepted but still run gmsh.

Read: `mesh_file`, `num_nodes`, `num_elements`, `element_types`,
`num_volume_elements` (inside `quality_metrics`), `faces`, `mesh_bbox_mm`,
`coordinate_frame` (always `twin`) and `placement_baked`.

- `num_volume_elements` must be greater than zero. Zero means
  gmsh produced only surface elements and nothing can be solved.
- `mesh_bbox_mm` should match the part's bounding box. A mismatch means the
  wrong file was meshed.

### 4. Identify the faces by coordinates

The `faces` table has one row per gmsh surface group: `name` (`Surface1`,
`Surface2`, ...), `bbox_mm`, `centroid_mm`, `area_mm2` and `normal`. The
names say nothing about which face is which. For each face the user
described:

1. Translate the words into a coordinate test: "wall-mount face at x = 0" is
   the group whose `bbox_mm` has min x = max x = 0 (a planar face) and whose
   normal is along x.
2. Find every group that passes. A bolt hole is usually several cylindrical
   groups, not one; a flat face split by a feature may be two.
3. If no group matches, or several plausible ones do, show the user the
   candidates with their coordinates and ask. Never guess.
4. State which group you chose for each role and the coordinates that
   justified it, so the reviewer can check.

The mesh is in the twin's frame, so loads given in twin x, y, z act along the
same axes in `calculix.run_fea`. Later, `freecad.list_named_faces` with
`mesh_file` returns the same table without re-meshing.

### 5. Check quality

Call `calculix.validate_mesh` with `mesh_file` and `max_aspect_ratio` (the
user's threshold, or the default 10 if they have none, said explicitly).
Read `valid`, `element_count`, `node_count`, `max_aspect_ratio` and `issues`.

`freecad.generate_mesh` itself reports no angle or aspect-ratio metrics, so
this is the only quality check. If `valid` is false, re-mesh (smaller
`element_size` near thin features, or second order) and validate again.

### 6. Report

- `mesh_file`, element size and order, node and volume-element counts
- The validation verdict and `max_aspect_ratio`
- The surface group chosen for each role, with its coordinates
- The node id and revision of the geometry that was meshed

A mesh is not recorded in the twin by itself; it is recorded as part of the
simulation result that uses it. Say that if the user asks where it is saved.

## Checks before you report

- [ ] Meshed the staged file of the exact revision, not a remembered path
- [ ] `output_format` was `inp` and volume elements are present
- [ ] Mesh bounding box matches the part
- [ ] Every fixed and load face identified by bbox, centroid and normal, not by name
- [ ] `calculix.validate_mesh` run and passed, or its absence stated
- [ ] Element size choice explained

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `freecad` or `calculix` | Adapter container down | Tell the user which; do not loop. |
| File not found on `input_file` | Passed a work product id or a stale path | Stage again with `twin.stage_work_product_file`. |
| "gmsh meshing failed" | Bad geometry (open shell, self-intersection) or a size too small for gmsh | Try a larger `element_size`; if it still fails, the CAD needs repair. Tell the user. |
| `num_volume_elements` is 0 | Input is a surface or non-solid shape | Check the CAD is a closed solid. |
| `faces` empty | `output_format` was not `inp` | Re-mesh with `inp`. |
| No face matches the description | Description in a different frame, or the face is split | Show candidates; ask the user. |
| `calculix.validate_mesh` not in your list | Connected on `mechanical` | Ask for `?profile=simulation`; report the mesh as unvalidated meanwhile. |
| Meshing very slow or timed out | Element size far too small for the part | Coarsen, then refine only if convergence needs it. |

## Limits

- Tetrahedra only (C3D4 or C3D10), uniform target size. No local refinement
  regions, no hex or shell elements.
- STEP input only.
- Quality is checked by aspect ratio in `calculix.validate_mesh`; no Jacobian
  or minimum-angle metric is exposed.
- A mesh belongs to one geometry revision. After the part changes, re-stage
  and re-mesh; do not reuse the old file.
