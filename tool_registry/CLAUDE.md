# tool_registry

MCP-based tool access layer. Manages containerized tool adapters (KiCad, FreeCAD, CalculiX, SPICE, distributor APIs) and provides the MCP server that domain agents communicate with.

## Layer & Dependencies

- **Layer**: 3
- **May import from**: `mcp_core`, `observability`, `pydantic`, standard library
- **Do NOT import from**: `skill_registry`, `twin_core`, `orchestrator`, `domain_agents`, `api_gateway`, `digital_twin`

## Key Files

- `registry.py` -- `ToolRegistry` catalog with capability discovery
- `execution_engine.py` -- `ExecutionEngine` for tool invocation with timeout and retry
- `container_runtime.py` -- `ContainerRuntime`, `DockerRuntime`, `InMemoryRuntime`, `ContainerConfig`
- `tool_metadata.py` -- `AdapterInfo`, `AdapterStatus`, `ToolCapability`
- `mcp_server/` -- MCP server implementation
  - `server.py` -- Server bootstrap and lifecycle
  - `handlers.py` -- JSON-RPC request handlers
- `tools/` -- Individual tool adapters
  - `calculix/` -- FEA analysis (CalculiX)
  - `freecad/` -- CAD operations (FreeCAD)
  - `kicad/` -- PCB/schematic validation (KiCad, read-only in Phase 1)
  - `spice/` -- Circuit simulation (ngspice)
  - `digikey/`, `mouser/`, `nexar/`, `distributors/` -- Component distributor APIs

## Testing

```bash
ruff check tool_registry/
mypy --strict tool_registry/
pytest tests/unit/test_tool_registry*.py -v
```

## Conventions

- All tool adapters run in Docker containers -- never invoke tools on the host directly
- Use `InMemoryRuntime` in unit tests -- never require Docker
- KiCad is read-only in Phase 1 (ERC/DRC/BOM/Gerber export); write capabilities come in Phase 2
- Each adapter must declare its capabilities via `ToolCapability` metadata
- MCP server handlers must validate all incoming requests against `mcp_core.schemas`
- Add observability instrumentation (Logs, Metrics, Traces) to all tool invocations — see [Observability Requirements in root CLAUDE.md](../CLAUDE.md#observability-requirements) for all 7 levels
