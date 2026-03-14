# generate_mesh

Generate finite element mesh from CAD geometry using FreeCAD.

## What it does

1. Takes a CAD model work_product ID and meshing parameters as input
2. Validates the input file extension and algorithm choice
3. Invokes the FreeCAD meshing tool via MCP to generate a finite element mesh
4. Evaluates mesh quality metrics against user-defined thresholds
5. Returns the mesh file path, statistics, and quality assessment

## Tools Required

- `freecad.generate_mesh` -- FreeCAD finite element mesh generation

## Input

- `work_product_id` -- ID of the CAD model work_product in the Digital Twin
- `cad_file` -- Path to the input CAD file (.step, .stp, .stl, .brep)
- `element_size` -- Target element size in mm (default: 1.0)
- `algorithm` -- Meshing algorithm: netgen, gmsh, or mefisto (default: netgen)
- `output_format` -- Output mesh format: inp, unv, or stl (default: inp)
- `min_angle_threshold` -- Minimum acceptable element angle in degrees (default: 15.0)
- `max_aspect_ratio_threshold` -- Maximum acceptable aspect ratio (default: 10.0)
- `refinement_regions` -- Optional list of refinement region definitions

## Output

- `mesh_file` -- Path to the generated mesh file
- `num_nodes` -- Number of mesh nodes
- `num_elements` -- Number of mesh elements
- `element_types` -- List of element types used (e.g., C3D10, C3D4)
- `quality_metrics` -- Mesh quality metrics (min_angle, max_aspect_ratio, avg_quality, jacobian_ratio)
- `quality_acceptable` -- Whether the mesh meets all quality thresholds
- `quality_issues` -- List of human-readable quality issues found
- `algorithm_used` -- The meshing algorithm that was used
- `element_size_used` -- The element size that was used

## Limitations

- Supported input formats: STEP (.step/.stp), STL (.stl), BREP (.brep)
- Quality assessment is based on min_angle and max_aspect_ratio thresholds only
- Refinement regions are passed through to FreeCAD but not validated locally
- Does not perform adaptive mesh refinement based on error estimation
