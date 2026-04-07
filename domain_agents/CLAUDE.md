# domain_agents

Domain specialist agents -- one per engineering discipline. Each agent uses PydanticAI for LLM orchestration and communicates with tools exclusively through the MCP protocol via the skill system.

## Layer & Dependencies

- **Layer**: 3
- **May import from**: `skill_registry`, `twin_core`, `observability`, `mcp_core` (via skill bridge), `pydantic`, standard library
- **Do NOT import from**: `orchestrator`, `api_gateway`, `tool_registry` (tools accessed only via MCP)

## Key Files

- `base_agent.py` -- Base agent class with common PydanticAI setup
- `shared/skills/` -- Skills shared across multiple agents
- `mechanical/` -- Mechanical engineering agent
  - `agent.py` -- Agent orchestration logic
  - `adapters/` -- Domain-specific adapters (FreeCAD, CalculiX)
  - `skills/` -- Mechanical skills (stress validation, meshing, tolerances)
- `electronics/` -- Electronics engineering agent (ERC, DRC, BOM, power budget)
- `firmware/` -- Firmware agent
- `simulation/` -- Simulation and validation agent
- `compliance/` -- Certification and compliance agent
- `supply_chain/` -- Supply chain and BOM risk agent

## Testing

```bash
ruff check domain_agents/
mypy --strict domain_agents/
pytest tests/unit/test_*agent*.py -v
```

## Conventions

- Each agent directory follows the same structure: `agent.py`, `adapters/`, `skills/`
- Each skill follows the strict directory convention: `definition.json`, `SKILL.md`, `schema.py`, `handler.py`, `tests.py`
- Agents NEVER call tools directly -- all tool access goes through `McpBridge` / skill system
- Use `pydantic_ai.models.test.TestModel` in tests -- NEVER call a live LLM
- Agents read from and propose changes to the Digital Twin -- they do not own state
- Add observability instrumentation (Logs, Metrics, Traces) to all agent operations — see [Observability Requirements in root CLAUDE.md](../CLAUDE.md#observability-requirements) for all 7 levels
