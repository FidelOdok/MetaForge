# Skill Registry

Skill management layer — `skill_registry/`.

- `registry.py` — skill catalog with auto-discovery.
- `loader.py` — dynamic loading from `definition.json` files.
- `mcp_bridge.py` / `mcp_client_bridge.py` / `registry_bridge.py` — the bridge from skill tool calls to the MCP protocol, both in-memory (test double) and real variants; `bridge_factory.py` picks which one at construction time.
- `skill_base.py` — abstract base class every skill implements.
- Skills live under each domain agent's `skills/` dir following a strict convention: `definition.json`, `SKILL.md`, `schema.py`, `handler.py`, `tests.py`. See `docs/skill_spec.md` for the full spec.

Related: [MCP Core & Tool Registry](mcp-core-and-tool-registry.md) for what's on the other side of the bridge.
