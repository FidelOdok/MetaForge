---
updated: 2026-08-11
---

# Digital Twin

The WorkProduct graph that owns all design state — `twin_core/`.

- **Dual-backend by design, not a stub** (verified 2026-08-11): `twin_core/graph_engine.py` defines an abstract `GraphEngine` + `InMemoryGraphEngine`. `twin_core/neo4j_graph_engine.py` (837 lines) is a genuine async-driver `Neo4jGraphEngine` with real Cypher queries and tracing spans.
- `twin_core/api.py`'s `InMemoryTwinAPI.create_from_env()` auto-detects `NEO4J_URI` / `METAFORGE_GRAPH_BACKEND=neo4j` and swaps in the Neo4j engine — otherwise falls back to in-memory.
- **Gotcha**: `api_gateway/server.py` currently imports plain `InMemoryTwinAPI` at module scope. Default/dev wiring is in-memory; Neo4j is opt-in production. The class name `InMemoryTwinAPI` is misleading once Neo4j is configured — treat it as "TwinAPI, backend chosen at construction time," not "always in-memory."
- **Legacy dir**: an older `digital_twin/` directory still exists alongside `twin_core/` at repo root — don't confuse the two, `twin_core/` is current. See [Repository Structure Drift](repository-structure-drift.md).
- Project membership on a node is stored in two places that must both be kept in sync — see [Twin Project Scoping](twin-project-scoping.md).

See `docs/twin_schema.md` for the schema spec.
