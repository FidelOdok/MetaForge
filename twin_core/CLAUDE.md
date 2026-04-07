# twin_core

Digital Twin core -- the single source of design truth. Provides the Digital Thread (work product graph), constraint engine, gate engine, versioning, validation, knowledge store, and standards mapping (AAS, SysML).

## Layer & Dependencies

- **Layer**: 2
- **May import from**: `shared/types`, `observability`, `pydantic`, standard library
- **Do NOT import from**: `skill_registry`, `mcp_core`, `orchestrator`, `domain_agents`, `api_gateway`, `digital_twin`, `tool_registry`

## Key Files

- `api.py` -- `TwinAPI` facade and `InMemoryTwinAPI` for testing
- `graph_engine.py` -- Core in-memory graph CRUD and traversal
- `neo4j_graph_engine.py` -- Neo4j-backed graph engine implementation
- `models/` -- Pydantic models: `WorkProduct`, `Constraint`, `Relationship`, `Version`, `BomItem`, `Component`, `DesignElement`, `DeviceInstance`
- `constraint_engine/` -- Cross-domain constraint validation, YAML rule loading, resolver
- `gate_engine/` -- Design review gate evaluation and scoring
- `validation_engine/` -- Schema validation for work product types (JSON Schema based)
- `versioning/` -- Branch, merge, diff operations on the Digital Thread
- `knowledge/` -- Embedding service and knowledge store
- `aas/` -- Asset Administration Shell export (AASX packaging)
- `sysml/` -- SysML v2 mapping and serialization

## Testing

```bash
ruff check twin_core/
mypy --strict twin_core/
pytest tests/unit/test_twin*.py tests/unit/test_constraint*.py tests/unit/test_gate*.py -v
```

## Conventions

- All state mutations go through `TwinAPI` -- never modify the graph directly from outside
- Models use Pydantic v2 with strict validation
- Constraint rules are defined in YAML files under `constraint_engine/rules/`
- Add observability instrumentation (Logs, Metrics, Traces) to all public methods — see [Observability Requirements in root CLAUDE.md](../CLAUDE.md#observability-requirements) for all 7 levels
- `InMemoryTwinAPI` is the canonical test double -- use it in all unit tests
- Gate engine scoring must be deterministic and auditable
