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
- `cad_file` -- Path to the input CAD file (.step or .stp; STL and BREP are refused)
- `element_size` -- Target element size in mm (default: 1.0)
- `algorithm` -- netgen, gmsh, or mefisto (default: netgen). All three run gmsh; netgen and mefisto are accepted names, not separate meshers
- `output_format` -- Output mesh format: inp, unv, or stl (default: inp)
- `min_angle_threshold` -- Minimum acceptable dihedral angle in degrees (default: 10.0; a plain gmsh tet mesh of a box measures about 13)
- `max_aspect_ratio_threshold` -- Maximum acceptable aspect ratio (default: 10.0)
- `refinement_regions` -- Not supported yet; a non-empty list is refused

## Output

- `mesh_file` -- Path to the generated mesh file
- `num_nodes` -- Number of mesh nodes
- `num_elements` -- Number of mesh elements
- `element_types` -- List of element types used (e.g., C3D10, C3D4)
- `quality_metrics` -- Measured from the element geometry of `.inp` output: min_angle (minimum dihedral angle, deg), max_aspect_ratio (longest over shortest edge), avg_quality (mean radius ratio, 1.0 is a regular tet). jacobian_ratio is not measured and stays 0
- `quality_acceptable` -- Whether the mesh meets all quality thresholds
- `quality_issues` -- List of human-readable quality issues found
- `algorithm_used` -- The meshing algorithm that was used
- `element_size_used` -- The element size that was used

## Limitations

- Supported input format: STEP (.step/.stp) only
- Quality is measured only for `.inp` output. For `unv` or `stl` output the metrics are absent, and the skill reports "element quality was not measured" and `quality_acceptable: false` rather than passing an unmeasured mesh
- Quality assessment uses min_angle, max_aspect_ratio and degenerate (zero-volume) elements; meshes above 200,000 elements are sampled evenly
- Refinement regions are not supported
- Does not perform adaptive mesh refinement based on error estimation
