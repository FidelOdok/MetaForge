# Run Cycle 1 + 2 UAT (Claude-driven)

Launch the `uat-validator` subagent to drive the live MetaForge MCP
server through scenario-based user-acceptance tests. Validates the
**user experience** of every Cycle 1 + Cycle 2 deliverable from a
Claude-as-real-user perspective. Complement to the wire-level pytest
UAT in `tests/uat/`.

## Instructions

Launch the `uat-validator` agent (defined in
`.claude/agents/uat-validator.agent.md`) using the Agent tool with
`subagent_type: "uat-validator"`.

The argument selects which **tier** of scenarios to run.

### Default (no arguments): Tier 0 — golden flow only

If the user provides no arguments, use this prompt:

> Run the Tier-0 golden-flow UAT scenarios.
>
> Steps:
> 1. Pre-flight: confirm the MetaForge MCP server responds via
>    `mcp__metaforge__knowledge_search`.
> 2. Read every `## Scenario:` block from
>    `tests/uat/scenarios/tier0/*.md`.
> 3. Execute each scenario per the contract in your agent
>    definition.
> 4. Generate `docs/uat/uat-claude-driven-report-<today>.md`.
> 5. File a Linear follow-up for any FAIL (Cycle 3, P1.15
>    milestone).
> 6. Hand off with the one-line summary, report path, and any
>    follow-up issue ids.

### Specific tier (`--tier 0|1|2|all`)

If the user provides `--tier 1`, `--tier 2`, or `--tier all`,
substitute the tier in the directory globs:

> Run the Tier-$TIER UAT scenarios.
>
> Read every `## Scenario:` block from:
> - Tier 0: `tests/uat/scenarios/tier0/*.md`
> - Tier 1: `tests/uat/scenarios/tier1/*.md` (every group: knowledge,
>   cadquery, calculix)
> - Tier 2: `tests/uat/scenarios/tier2/*.md`
> - all: every tier in order, 0 → 1 → 2.
>
> Execute, report, file gaps as in the default flow.

### Filtered run (`--only "<scenario title substring>"`)

For reproducing a single failing scenario:

> Run only scenarios whose title contains "$ARGUMENT" across every
> tier directory. Skip the rest. Report as usual but with a single
> scenario detail section.

### Agent Configuration

When launching the agent, use:
- `subagent_type`: `"uat-validator"` — invokes the custom agent from
  `.claude/agents/uat-validator.agent.md`.
- The agent has access to all `mcp__metaforge__*` tools, the Linear
  MCP tools (for filing gap follow-ups), Grafana MCP (for Tier-2
  observability probes), and Read/Write/Bash/Glob/Grep for the
  scenario corpus and report writing.

### Required pre-flight (before invoking the agent)

The MetaForge MCP server is launched automatically by Claude Code's
`.mcp.json` at startup. If `/mcp` doesn't show `metaforge` connected,
abort and tell the user to fix `.mcp.json` first — the agent's
pre-flight check will surface the same problem but earlier.

### Output

The agent's last message must contain:
1. One-line summary: `X scenarios, Y PASS, Z FAIL, W SKIP/BLOCKED`.
2. Report path (`docs/uat/uat-claude-driven-report-<date>.md`).
3. List of Linear follow-up issue ids for any FAILs.

Forward this to the user verbatim.
