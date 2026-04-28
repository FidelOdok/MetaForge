# UAT Tier-1 calculix — Claude-driven first run (2026-04-28)

**Scenario set**: `tests/uat/scenarios/tier1/calculix.md` (5 scenarios)
**Validates**: MET-337, MET-340
**Tier**: 1 (cycle gate cadence)
**Path**: validator surrogate
**Surrogate driver**: `scripts/run_tier1_calculix_validator_surrogate.py`
**Overall verdict**: **FAIL** — 5 PASS, 5 FAIL

---

## Summary

| # | Scenario | Verdict |
|---|----------|---------|
| 1 | tool/list reports all four calculix tools | **FAIL** (0 tools registered) |
| 2 | validate_mesh accepts an empty / synthetic mesh ref | 2 PASS / 1 FAIL |
| 3 | run_fea rejects missing required arguments cleanly | 1 PASS / 1 FAIL |
| 4 | extract_results on non-existent run id fails cleanly | 2 PASS |
| 5 | run_thermal manifest is reachable | **FAIL** (manifest empty) |

All FAILs trace to **two real gaps in the calculix-adapter container**, both filed forward.

---

## Gaps filed forward

### MET-379 — calculix adapter exposes REST `/tools/<id>` instead of JSON-RPC `/mcp`

The cadquery adapter responds to `POST /mcp` with JSON-RPC `tool/list` / `tool/call`. The calculix adapter has only:

```
POST /tools/calculix.run_fea
POST /tools/calculix.extract_results
GET /health
```

So when `metaforge.mcp` boots with `METAFORGE_ADAPTER_CALCULIX_URL=http://localhost:8200`, the registration's `tool/list` JSON-RPC call hits 404, no tools register, and every subsequent `tool/call` returns `Tool not found: calculix.*`.

This is the **central architectural failure** — the unified server's promise to route MCP calls across all containerised adapters depends on every adapter exposing the same `/mcp` JSON-RPC endpoint that cadquery does. P1.

### MET-380 — calculix adapter silently disables `validate_mesh` + `run_thermal` when ccx binary missing

`GET /health` reports:
```json
{"status": "degraded", "tools_available": 2, "ccx_available": false}
```

Only 2 of the 4 calculix tools are exposed. `validate_mesh` and `run_thermal` are silently dropped because the `ccx` binary isn't bundled in the image. The manifest contract should be stable; runtime errors belong at handler invocation time, not at boot.

---

## Reproducer

```bash
docker compose up -d calculix-adapter
.venv/bin/python scripts/run_tier1_calculix_validator_surrogate.py
# Will FAIL on Scenario 1 (0 tools registered) — root cause is MET-379
```

---

## Notes on path

Same surrogate pattern as Tier-1 cadquery (which now passes 14/14). The calculix gaps are in the adapter container itself, not in the unified routing — so once MET-379 lands, this surrogate should immediately surface much closer to PASS, with MET-380 as the remaining manifest-stability gap.
