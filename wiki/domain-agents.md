---
updated: 2026-08-11
---

# Domain Agents

One agent per engineering discipline — `domain_agents/`. All of these are real implementations, not stubs (verified 2026-08-11):

- `mechanical/` — by far the largest (~5050 lines: `agent.py`, `pydantic_ai_agent.py`, `workflows.py`, `writeback.py`). The first end-to-end vertical per CLAUDE.md (CAD → FEA → Twin update).
- `electronics/`, `firmware/`, `simulation/` — similarly structured (`agent.py` + `pydantic_ai_agent.py`, roughly 1.3-1.4K lines each).
- `supply_chain/` (`alt_parts.py`, `risk_scorer.py`, `models.py`) and `compliance/` (`checklist_generator.py`, `evidence_tracker.py`) also have real logic — beyond the 6-7 disciplines CLAUDE.md names for Phase 1, worth checking here before assuming something is out of scope.
- Shared base: `domain_agents/base_agent.py` — start there before any specific agent.

Related: [Skill Registry](skill-registry.md) for how agents expose atomic capabilities, [MCP Core & Tool Registry](mcp-core-and-tool-registry.md) for how they reach real tools.
