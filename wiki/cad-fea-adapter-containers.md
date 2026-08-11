# CAD/FEA Adapter Containers

The MCP sidecar is built from the gateway `Dockerfile`, which does **not** ship native CAD/FEA runtimes (cadquery/OCP, FreeCAD, CalculiX, kicad-cli). Those run as separate adapter containers — `cadquery-adapter`, `freecad-adapter`, `calculix-adapter`, `occt-converter` — and `tool_registry/bootstrap.py` connects to them via `METAFORGE_ADAPTER_*_URL` env vars.

**The trap**: if an adapter container is down or unreachable at sidecar startup, `bootstrap_tool_registry` logs a warning and **silently falls back to the in-process adapter** (e.g. `tool_registry/tools/cadquery/operations.py`). That in-process fallback registers its tools fine — so `/health` shows the adapter present with a full tool count — but every actual call hits a missing binary and surfaces as a generic `MCP error -32001: Tool execution failed`. This looks like a bad-parameter error but is really "native runtime absent." Registration is startup-only; there's no re-probe, so a container that comes up late never gets picked up without a sidecar restart.

Pure-Python adapters (knowledge/twin/project/memory/constraint) are unaffected — they don't need a native runtime.

**Fix sequence (containers must be up BEFORE the sidecar bootstraps):**
1. Check whether the adapter images are even built (`docker images | grep adapter`) — they're defined in `docker-compose.yml` with no compose profile, so they aren't part of every environment's default `up` subset. Build if missing.
2. `docker compose up -d occt-converter cadquery-adapter freecad-adapter calculix-adapter` and wait for each to report healthy.
3. `docker compose restart mcp-http` — this re-runs bootstrap and should log `Registered remote adapter adapter_id=<name>` instead of `Remote adapter unreachable — falling back to in-process`.

Possible product improvement worth a Linear issue if not already filed: the silent in-process fallback masks an adapter outage as a generic `-32001` — a distinct "adapter unavailable" error code would save real diagnosis time.

Related: [MCP HTTP Sidecar](mcp-http-sidecar.md), [FreeCAD & CAD Authoring Conventions](freecad-and-cad-authoring.md).
