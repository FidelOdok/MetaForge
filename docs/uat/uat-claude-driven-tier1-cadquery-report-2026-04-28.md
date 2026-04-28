# UAT Tier-1 cadquery — Claude-driven first run (2026-04-28)

**Scenario set**: `tests/uat/scenarios/tier1/cadquery.md` (6 scenarios)
**Validates**: MET-337, MET-340, MET-373
**Tier**: 1 (cycle gate cadence)
**Path**: validator surrogate (parent Claude Code session pre-dates `.mcp.json`; canonical path is `/uat-cycle12 --tier 1`)
**Surrogate driver**: `scripts/run_tier1_cadquery_validator_surrogate.py`
**Elapsed**: 11.94s wall
**Overall verdict**: **PASS** — 14/14 assertions

---

## Summary

| # | Scenario | Verdict |
|---|----------|---------|
| 1 | create a parametric box and inspect properties | PASS (5/5) |
| 2 | invalid shape_type returns a clean tool error | PASS (2/2) |
| 3 | create a cylinder | PASS (2/2) |
| 4 | bounding box reports correct dimensions | PASS (2/2) |
| 5 | missing required parameter | PASS (2/2) |
| 6 | tool/list reports all seven cadquery tools | PASS (1/1) |

---

## What this validated

This is the first Tier-1 run since MET-373 closed in PR #140. The fix had three layers:

1. **`.mcp.json`** sets `METAFORGE_ADAPTER_CADQUERY_URL=http://localhost:8101` so the standalone `python -m metaforge.mcp` knows where the adapter container is.
2. **`docker-compose.yml`** exposes cadquery-adapter port `8101:8101` to the host.
3. **`tool_registry/registry.py` + `metaforge/mcp/server.py` + `metaforge/mcp/__main__.py`** (this branch): the unified MCP server now registers remote adapters via a `_RemoteAdapterServer` shim that surfaces them through `list_adapter_servers()`, and `_tool_list` delegates to each adapter's `handle_request` rather than poking private `_tools` dicts. The bootstrap and stdio loops now share one event loop so aiohttp `ClientSession`s registered during bootstrap stay valid through tool calls.

Without these three layers, the surrogate would see `"tools": []` (the bug Tier-0's UAT-CLAUDE-T0 first run originally surfaced as MET-373).

---

## Evidence highlights

- `cadquery.create_parametric` with a 50×30×10 box: `volume_mm3=15000.0, surface_area_mm2=4200.0, cad_file=/tmp/uat-tier1-box.step`.
- `cadquery.get_properties` on the produced STEP file returns `properties.volume=15000.0` (matches step 1 within tolerance).
- `cadquery.create_parametric` with a cylinder (`r=25, h=50`) returns `volume_mm3≈98174.77` (within 0.01% of `π·25²·50`).
- `bounding_box` has `min_x/max_x/min_y/max_y/min_z/max_z` keys; the 100×60×20 box produces extents `[20.0, 60.0, 100.0]` (any axis order is accepted because cadquery's `box()` arg order swaps width/length).
- Invalid `shape_type: tetrahedron` returns JSON-RPC error `-32001` with `details: "Unsupported shape type: tetrahedron"`.
- After error, transport stays alive — subsequent `tool/list` returns 7 tools.

---

## Reproducer

```bash
# Pre-flight
docker compose up -d cadquery-adapter
curl -sf http://localhost:8101/health   # status healthy

# Run
.venv/bin/python scripts/run_tier1_cadquery_validator_surrogate.py
```

Exit 0 = all PASS; exit 1 = at least one FAIL.

---

## Path note

Surrogate path used because the parent Claude Code session pre-dates `.mcp.json`. Same fix lands the canonical `/uat-cycle12 --tier 1` path identically — the only difference is who spawns the standalone `python -m metaforge.mcp` (subagent vs harness). The MET-373 fix is in the unified server, not the surrogate.
