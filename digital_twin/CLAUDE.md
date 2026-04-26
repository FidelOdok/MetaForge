# digital_twin

Extended Digital Twin layer. Builds on `twin_core` to provide design thread management, gate engine integration, knowledge retrieval, and assistant-mode features (file watching, reconciliation).

## Layer & Dependencies

- **Layer**: 2
- **May import from**: `twin_core`, `observability`, `pydantic`, standard library
- **Do NOT import from**: `orchestrator`, `domain_agents`, `api_gateway`, `skill_registry`, `mcp_core`, `tool_registry`

## Key Files

- `thread/` -- Design thread management and conversation context
- `thread/gate_engine/` -- Gate engine integration for design review workflows
- `knowledge/` -- Knowledge store consumer, embedding service, and retrieval
  - `consumer.py` -- Event consumer for knowledge updates
  - `embedding_service.py` -- Vector embedding generation
  - `store.py` -- Knowledge store interface
- `assistant/` -- Assistant-mode features
  - `watcher.py` -- File system watcher for design file changes
  - `reconciler.py` -- Reconciles external edits with Twin state
  - `adapters/` -- Format-specific adapters for file watching

## Testing

```bash
ruff check digital_twin/
mypy --strict digital_twin/
pytest tests/unit/test_digital_twin*.py -v
```

## Conventions

- This module extends `twin_core` -- never duplicate models or logic that belongs there
- Assistant-mode watchers are read-only by default; writes require explicit approval
- Knowledge consumers must be idempotent -- replaying events should produce the same state
- Add `structlog` logging and OTel tracing spans to all public methods
- Adding a new `KnowledgeType`? Follow the [Knowledge Ingestion Playbook](../docs/architecture/knowledge-ingestion-playbook.md) — covers enum, consumer mapping, CLI, role allow-lists, validation, and eval coverage in one pass.
