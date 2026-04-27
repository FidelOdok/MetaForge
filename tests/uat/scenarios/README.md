# UAT Scenario Corpus (Track B)

Markdown scenarios driven by the `uat-validator` subagent through
`/uat-cycle12`. Each scenario validates Cycle 1 / Cycle 2 acceptance
from a **user-as-Claude** perspective: does Claude correctly pick the
tool? Does the response schema make sense? Are auth errors actionable?

The wire-level half is in `tests/uat/cycle{1,2}/*.py` (pytest). This
directory is the user-experience half.

## Layout

```
tier0/   # 1 scenario, every PR / nightly — fast smoke
tier1/   # 6–10 per tool group, Cycle gates
  knowledge.md
  cadquery.md
  calculix.md
tier2/   # 2–3 observability probes, weekly
  staleness-probe.md
  provenance-probe.md
  dedup-probe.md
```

## Scenario format

Every scenario is one `## Scenario:` block with this exact shape so
the agent can parse them deterministically:

```markdown
## Scenario: <human-readable title>
Validates: MET-XXX[, MET-YYY]
Tier: 0 | 1 | 2

### Given
- Pre-conditions, written as bullet points.
- Each bullet is something the agent must establish before calling
  the When step. If a Given can't be met, the scenario reports
  BLOCKED, not FAIL.

### When
- Steps the agent must execute, in order.
- Each step is one MCP tool call OR one observation (e.g. read a
  file, query Loki, query Prometheus).
- Use natural language — the agent maps it to the right MCP tool.

### Then
- Assertions. Each is a boolean condition.
- The agent verifies each against the captured responses.
- All Then bullets must pass for the scenario to PASS.
```

## Running

```
/uat-cycle12               # Tier 0 (default)
/uat-cycle12 --tier 1      # all Tier-1 groups
/uat-cycle12 --tier all    # 0 + 1 + 2
/uat-cycle12 --only "ingest then search"
```

The agent generates `docs/uat/uat-claude-driven-report-<date>.md`
and files Linear follow-ups for any FAIL.

## Editing scenarios

Just edit the markdown — no agent / command changes needed. The
agent reads at runtime. Keep the format stable so parsing doesn't
drift.
