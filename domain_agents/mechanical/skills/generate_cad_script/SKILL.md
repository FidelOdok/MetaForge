# generate_cad_script

Generate and execute CadQuery Python scripts from natural language descriptions to produce 3D CAD models.

## What it does

1. Takes a natural language description and optional constraints as input
2. Builds a CadQuery Python script (deterministic fallback; in agent loop, LLM generates the script)
3. Executes the script via the sandboxed `cadquery.execute_script` MCP tool
4. Returns the generated CAD file path, script text, and geometric metadata

## Tools Required

- `cadquery.execute_script` -- Sandboxed CadQuery script execution

## Input

- `work_product_id` -- UUID of the CAD model work_product in the Digital Twin
- `description` -- Natural language description of the desired 3D model
- `constraints` -- Design constraints dict (dimensions, wall thickness, etc.)
- `material` -- Material name for metadata (default: aluminum_6061)
- `output_format` -- Output file format: step, stl, brep (default: step)

## Output

- `cad_file` -- Path to the generated CAD file
- `script_text` -- The CadQuery Python script that was executed
- `volume_mm3` -- Volume in cubic millimeters
- `surface_area_mm2` -- Surface area in square millimeters
- `bounding_box` -- Axis-aligned bounding box

## Limitations

- Deterministic script builder only produces simple parametric boxes
- Full capability requires the LLM agent to generate CadQuery scripts upstream
- Script execution is sandboxed: no file I/O, no network, no imports beyond cadquery/math
- Maximum 200 lines per script
