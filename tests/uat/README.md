# `tests/uat/` — Cycle 1 + 2 User Acceptance Tests

Level-11 UAT per [`docs/testing-strategy.md`](../../docs/testing-strategy.md).
Validates every Cycle 1 + Cycle 2 acceptance bullet against the live
stack.

## Two complementary tracks

| Track | Lives in | Runner | Cadence |
|---|---|---|---|
| **A · Wire-level** | `tests/uat/cycle1/`, `tests/uat/cycle2/`, `test_golden_flow.py` | pytest | Every PR |
| **B · Claude-in-loop** | `tests/uat/scenarios/` (markdown scenarios) | `uat-validator` subagent + `/uat-cycle12` | Tier-0 every PR; Tier-1 milestone gates; Tier-2 weekly |

Track A is the wire-level half — it answers *does the contract hold?*
Track B is the user-experience half — it answers *can a real Claude
session use it?*

## Quick start

```bash
# Bring up the dev backends (one-time)
docker compose up -d postgres neo4j

# Run Track A
pytest tests/uat/ --uat --integration -v --tb=short
```

`--uat` opts in to `@pytest.mark.uat`-tagged tests; most also need
`--integration` because they hit real backends. Run the whole suite
with both flags.

Run just one layer:

```bash
pytest tests/uat/cycle1/test_l3_quality_signals.py --uat --integration -v
```

The golden flow is the single most important test — if it passes, the
platform's prime contract works end-to-end:

```bash
pytest tests/uat/test_golden_flow.py --uat --integration -v
```

## Layout

```
tests/uat/
  __init__.py
  conftest.py                       # shared fixtures: real LightRAG svc, mcp subprocess
  README.md                         # this file
  cycle1/                           # Track A — Cycle 1 (Context Engineering)
    test_l0_persistence.py          # MET-292, 304, 305
    test_l1_retrieval.py            # MET-293, 307, 335, 336, 346
    test_l2_context_assembly.py     # MET-313, 315, 316, 317, 319, 320, 332
    test_l3_quality_signals.py      # MET-322, 323, 324, 326, 333, 334
    test_l4_extension_recipes.py    # MET-314, 329, 331
  cycle2/                           # Track A — Cycle 2 (MCP harness)
    test_l1_mcp_server.py           # MET-306, 337
    test_l2_auth_and_config.py      # MET-338, 339
    test_l3_external_harness.py     # MET-340
    test_l4_docs_links.py           # MET-341, 342
  test_golden_flow.py               # Track A — full-stack golden path
  scenarios/                        # Track B — markdown scenarios (added in follow-up)
    tier0/
    tier1/
    tier2/
```

## Linear traceability

Every test name embeds the MET id of the issue it validates
(e.g. `test_met316_role_scope_narrows_to_allowed_types`). Failures
should map directly back to a Cycle 1 or Cycle 2 ticket.

The full bullet-by-bullet mapping lives in
[`docs/uat/cycle-1-2-acceptance-matrix.md`](../../docs/uat/cycle-1-2-acceptance-matrix.md).

## Skip semantics

Tests skip rather than fail when the **environment** isn't ready:

* `--uat` not passed → all UAT tests skipped.
* `--integration` not passed → tests that need live backends skipped.
* Backends not reachable → individual fixtures skip with a clear reason
  (e.g. `Neo4j not reachable at localhost:7687`).
* `cadquery` package not installed → CAD-handler tests skip; manifest
  assertions still run.

A test that's skipped because the environment isn't ready is **not** a
gap. A test that **fails** is a gap and should produce a Linear
follow-up.

## Reporting

Track A produces `docs/uat/uat-report-<YYYY-MM-DD>.md` after each run
(generated from the pytest-json-report output). Track B produces
`docs/uat/uat-claude-driven-report-<YYYY-MM-DD>.md`. Both link to any
Linear gap follow-ups they file.

## Why Level 11

`docs/testing-strategy.md` line 215 lists Level-11 UAT as the project's
biggest known gap. This directory closes it. Acceptance criteria from
each Cycle 1/2 issue map to exactly one assertion here, so a green
suite is the project's first defensible answer to *"have we actually
validated what we shipped?"*.
