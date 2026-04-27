# Tier-0 — Golden flow (every PR / nightly)

One scenario, full critical path. If this passes the platform's prime
contract works end-to-end through Claude. If it fails nothing else
matters.

## Scenario: Claude ingests a doc, searches it, then generates a CAD part
Validates: MET-337, MET-346, MET-293, MET-335, MET-336
Tier: 0

### Given
- The MetaForge MCP server is reachable (the `/mcp` panel in Claude
  Code lists `metaforge` as connected).
- `knowledge.search`, `knowledge.ingest`, and
  `cadquery.create_parametric` appear in `tool/list`.

### When
1. Call `knowledge.ingest` with:
   - `content`: "MetaForge UAT Tier-0 marker phrase. The SR-7
     bracket is fabricated from titanium grade 5; the previous
     aluminium 6061 prototype failed thermal-cycle testing."
   - `source_path`: `"uat://tier0/sr7-bracket.md"`
   - `knowledge_type`: `"design_decision"`
2. Call `knowledge.search` with `query`: "What material does the
   SR-7 bracket use?" and `top_k`: 3.
3. Call `cadquery.create_parametric` with:
   - `shape_type`: `"box"`
   - `parameters`: `{ "width": 50, "length": 30, "height": 10 }`
   - `output_path`: `"/tmp/uat-tier0-bracket.step"`
   - `material`: `"titanium grade 5"`

### Then
- Step 1 returns `chunks_indexed >= 1` — the ingest contract works.
- Step 2 returns at least one hit whose `source_path` equals
  `"uat://tier0/sr7-bracket.md"` — search round-trips ingest.
- Step 2's top hit's `content` mentions "titanium grade 5" — the
  ranking signal landed on the right document.
- Step 3 returns `status: "success"` and a non-empty `cad_file`
  path — CadQuery handler accepted the request.
- The full sequence completes in under 60s of wall time.
