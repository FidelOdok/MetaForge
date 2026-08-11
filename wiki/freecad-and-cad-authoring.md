# FreeCAD & CAD Authoring Conventions

## Architecture: headless FreeCAD, no external server

FreeCAD CAD-authoring runs **headless**, with tools built around its Python API — there is no separate "FreeCAD AI" server. Rich authoring lives in `tool_registry/tools/freecad/` (one adapter, one container; `freecadcmd`, `QT_QPA_PLATFORM=offscreen`). Sessions are stateful: `tool_registry/tools/freecad/session.py`'s `FreecadSessionStore` holds a live FreeCAD document per `session_id` plus an object registry keyed by a stable `obj_id`, with TTL/LRU eviction.

Persistence goes through `api_gateway/twin/geometry_recorder.py` (`twin.commit_geometry`): base64 STEP → MinIO → `CAD_MODEL` WorkProduct with `content_hash` + `metadata.minio_object_key` → project link → viewer-ready GLB. This lives on the twin adapter (always in-process), never on the freecad adapter, because the freecad container can't reach MinIO or the twin directly.

**Solver architecture**: hybrid — analytic single-DOF joint kinematics (`api_gateway/constraint/kinematics.py`) drive the 10Hz live-drag interaction; a full FreeCAD solve + collision check happens on Apply.

**Container build gotcha**: the freecad-adapter image must use a single Python interpreter matching what FreeCAD's compiled `.so` targets (a python.org/Debian-system Python version mismatch makes `import FreeCAD` fail at runtime even though tools register fine). PartDesign/Sketcher workbench `Mod` dirs must also be on `PYTHONPATH`. Probe headless behavior first for any new FreeCAD op before building tooling around it — several ops that work fine in the GUI fail differently headless (e.g. `PartDesign::Thickness` fails headless; `Part.makeThickness` works instead).

## Naming: every part must be named

Every authored part must carry a meaningful `Label` (e.g. `fuselage`, `front_left_motor`) — never ship generic `Part_1..N`. Named parts are how the Twin viewer, BOM, and downstream analysis identify components. This is enforced server-side for the MCP authoring path: `FreecadSessionStore.register_object` stamps a real label (caller's name wins, else a unique `obj_id`) on every object, and `Import.export` writes those Labels into STEP `PRODUCT` entries — verify with `grep PRODUCT model.step`. Raw scripts run outside the MCP (e.g. directly in a container) bypass this — naming there is on the author.

## Colour: STEP is the only source of truth

Part colours in the Twin CAD viewer come **only** from the model's own STEP colours — never fabricate a render-time palette. Colour is design data; it must be versioned and reviewable like everything else, not computed at view time.

**Headless FreeCAD cannot author colour** — `ViewObject` is `None` even after `FreeCADGui.setupWithoutGUI()`, and `Import.export` writes zero colour entities, because colour is a GUI `ViewObject` property that doesn't exist in `freecadcmd`. So FreeCAD-authored STEPs come out uniform gray. The fix is a post-export step: `tools/occt-converter/colorize.py` bakes colour into the STEP via OCCT's XDE `ColorTool`, after FreeCAD export, in the occt-converter image (which has pythonocc; the FreeCAD adapter doesn't). This is a manual/generation step today — auto-wiring it into the authoring/commit/import flow is still open.

Related: [CAD/FEA Adapter Containers](cad-fea-adapter-containers.md), [Twin Project Scoping](twin-project-scoping.md).
