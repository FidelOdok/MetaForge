# MCP Core & Tool Registry

Two distinct layers, easy to conflate:

- **`mcp_core/`** — the thin, dependency-free wire-protocol layer. `protocol.py`, `schemas.py`, `client.py`, `transports.py`. No tool-specific logic lives here.
- **`tool_registry/`** — where the actual tool adapters live: `tool_registry/tools/{kicad,freecad,freecad_ai,cadquery,calculix,spice,digikey,mouser,nexar,distributors}/`, each with `adapter.py` + `config.py`. Wired up in `tool_registry/bootstrap.py` (per-adapter registry with env-var URL overrides, e.g. `METAFORGE_ADAPTER_CADQUERY_URL`).
- **Drift example (verified 2026-08-11)**: the KiCad adapter existed in the tree for a while but wasn't wired into the unified MCP server (`tool_registry/mcp_server/server.py`) until much later. "The adapter file exists under `tools/`" does not mean "the tool is reachable" — always check `bootstrap.py`'s registration, not just the directory listing.
- Heavy adapters (cadquery/freecad/calculix) run as **separate containers**, not in the gateway image — see [CAD/FEA Adapter Containers](cad-fea-adapter-containers.md) for why this causes silent `-32001` failures in dev.
- The dev MCP endpoint itself runs as a sidecar, not embedded in the gateway — see [MCP HTTP Sidecar](mcp-http-sidecar.md).

See `docs/mcp_spec.md` for the wire protocol spec and `docs/capability-matrix.md` for the full tool catalog and Phase-1 limits.
