# generate_cad_script

Generate and execute a CAD script (CadQuery or FreeCAD) from a natural language description to produce a 3D CAD model.

## What it does

1. Takes a natural language description and optional constraints as input
2. Builds a script in whichever dialect `backend` selects (deterministic CadQuery fallback if none supplied; in the agent loop, the LLM generates the script for either dialect)
3. Resolves a backend via `resolve_cad_backend` (`domain_agents/shared/cad_backend.py`), preferring `backend`, falling back to the other if unavailable, and executes:
   - CadQuery -- one call to the sandboxed `cadquery.execute_script` MCP tool
   - FreeCAD -- a 4-call session sequence (`open_session` → `execute_code` → `measure` → `export_model` → `close_session`, always closed), since FreeCAD's authoring surface is stateful
4. Returns the generated CAD file path, script text, and geometric metadata in the same shape regardless of which backend ran

## Tools Required

- `cadquery.execute_script` -- Sandboxed CadQuery script execution (primary)
- `freecad.open_session` / `freecad.execute_code` / `freecad.measure` / `freecad.export_model` / `freecad.close_session` -- Sandboxed FreeCAD script execution against a live session document (fallback, or requested explicitly via `backend`)

## Input

- `work_product_id` -- UUID of the CAD model work_product in the Digital Twin
- `description` -- Natural language description of the desired 3D model
- `script` -- The script to execute, in whichever dialect `backend` selects; if empty, a deterministic CadQuery fallback is built from `constraints`
- `backend` -- Which CAD kernel to run `script` against: `cadquery` (default) or `freecad`
- `constraints` -- Design constraints dict (dimensions, wall thickness, etc.)
- `material` -- Material name for metadata (default: aluminum_6061)
- `output_format` -- Output file format: step, stl, brep (default: step). Must be `step` when `backend` resolves to `freecad` -- its session export produces STEP only

## Output

- `cad_file` -- Path to the generated CAD file
- `script_text` -- The script that was executed (CadQuery may echo back a sandbox-transformed version; FreeCAD echoes back what was submitted)
- `volume_mm3` -- Volume in cubic millimeters
- `surface_area_mm2` -- Surface area in square millimeters
- `bounding_box` -- Axis-aligned bounding box

## Limitations

- Deterministic script builder only produces simple parametric boxes, and is CadQuery-only
- Full capability requires the LLM agent to generate CadQuery or FreeCAD scripts upstream
- Both dialects are sandboxed: no file I/O, no network, no imports beyond the injected safe set (`cadquery`/`math` for CadQuery; `FreeCAD`/`Part`/`math` for FreeCAD), max 200 lines per script
- FreeCAD's session export only produces STEP -- requesting `stl`/`brep` with `backend="freecad"` raises before execution
