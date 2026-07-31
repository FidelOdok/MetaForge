# Design-flow scenario evals

The eval flywheel for the gated design-flow harness: run reference
hardware/robotics projects through the **real** harness, capture what actually
happened, and let the gaps prioritise development.

```
   scenarios ──► experimentation ──► data ──► insights ──► development
      ▲                                                          │
      └──────────────────────────────────────────────────────────┘
```

This directory is **step 1–2** (scenarios + experimentation → structured data)
plus the first slice of step 3: `judge.py` scores work-product *quality*
against each scenario's `definition_of_done` (see below).

## Scenarios (`scenarios/*.json`)

A graded ladder from trivial to full robotics. Each fixture is a goal + the flow
to run + a `definition_of_done` (per-phase rubric, for the future scorer) +
`expected_deliverables` (types the run should record in the twin).

| Fixture | Level | Flow | Exercises |
|---|---|---|---|
| `l0_bracket` | 0 | `design_v1` | Mechanical only (CAD + FEA) |
| `l1_breakout` | 1 | `hardware_v1` | Electronics + firmware + the full lifecycle |

Add a level by dropping another JSON file in `scenarios/`.

## Runner (`run_scenarios.py`)

Drives each scenario through `POST /v1/runs` unattended — **auto-approving every
gate** — and writes a structured eval record per run.

```bash
# All scenarios, against a live gateway
python3 evals/run_scenarios.py --gateway http://fidel-dev:8000

# One scenario, repeated (LLM variance)
python3 evals/run_scenarios.py --scenario l0_bracket --repeat 5

# Custom timeout + report path
python3 evals/run_scenarios.py --cap-seconds 900 --out evals/report.json
```

Requires a running gateway with a real LLM + tools (the design flow actually
authors CAD, runs FEA, records decisions). Stdlib only.

## What it captures (per run)

- `terminal_status` — completed / failed / rejected / timeout
- `gates_reached` — how far through the lifecycle it got
- `gates[]` — each gate's reason + the deliverable snapshot at that point
- `deliverables_final` — work products actually recorded in the twin, by type
- `deliverable_completeness` — fraction of `expected_deliverables` met
- `duration_s`

Plus a per-scenario `summary` (completed-rate, avg completeness, avg duration).

## Reading the results (→ insights → development)

- **Low `deliverable_completeness`** on a phase → that phase's deliverable isn't
  landing (missing creation tool, or the brain isn't recording it).
- **`timeout` / stuck gate** → a phase the brain can't complete (tool errors,
  or an impossible required deliverable).
- **Present-but-thin deliverables** → the gap step-3 rubric scoring will catch;
  presence alone (this harness) can't.

The top recurring gaps become the next development: deliverable creation tools
for the missing types, constraint-as-gate-criteria, skill depth, adapter
reliability.

## Chat context-engineering suite (MET-570)

A second, independent suite: multi-turn conversation evals for the
harness-backed chat surface (`/v1/chat`), scoring the context engineering the
runs suite never touches — needle recall across turns, project-brief
adherence, window/trim telemetry, and tool-call trajectory quality.

```bash
# All chat scenarios, against a live gateway (METAFORGE_CHAT_HARNESS=1)
python3 evals/run_chat_scenarios.py --gateway http://fidel-dev:8000

# One scenario / one variant, threshold-gated
python3 evals/run_chat_scenarios.py --scenario chat_mem_needle --variant native --strict
```

### Scenarios (`chat_scenarios/*.json`)

Pure data: ordered `turns` (with `repeat` blocks for long-conversation
filler), declarative per-turn `checks` (`reply_contains`, `tool_called`,
`tool_arg_equals`, `no_duplicate_tool_calls`, ...), a provider `variants`
matrix (native tool-calling vs the text ReAct path), and `expected_today` —
checks **known** to fail on the current harness. Those score `xfail`
(confirmed baseline) instead of failing the run, and flip to `xpass` when the
fix ships, so re-running the identical command after MET-566/MET-568 measures
the improvement directly.

| Fixture | Scope | Exercises |
|---|---|---|
| `chat_mem_needle` | assistant | Fact recall a few turns later, both paths (MET-565 amnesia class) |
| `chat_mem_long_window` | assistant | Recall past the 20-turn history slice (xfail until MET-568) |
| `chat_brief_project` | project | Brief adherence: project known, twin writes carry `project_id` |
| `chat_brief_long_session` | project | Brief survives a long session (re-prepended every turn) |
| `chat_tool_bigobs` | assistant | Huge tool observations: 8KB native cap vs uncapped ReAct trace |
| `chat_tool_dedupe` | assistant | Multi-tool turn without duplicate identical calls |

### Rubrics

`chat_memory`, `chat_brief`, `chat_window`, `chat_tooluse` — each a pure
`evaluate_*` (unit-tested in CI, no gateway) plus a `score_*` the runner
dispatches. `agent.step` and `context.stats` are SSE-only, so the runner holds
a background subscription to each thread's `/stream` and slices events per
turn; replies come from refetching the thread (the agent turn completes inside
the message POST).

The wiring guard (`tests/unit/test_chat_eval_wiring.py`) fails CI when a
scenario's rubric isn't dispatched, a check id is unknown to `expected_today`,
or a check type isn't supported — the chat-suite sibling of
`test_eval_wiring.py`.

## Work-product quality judge (`judge.py`, MET-571)

The deterministic rubrics catch structural hollowness but can't grade
substance — a `design_decision` with a thin rationale passes them. `judge.py`
is an optional post-processing pass over **either suite's report**: for each
run that created a project, it fetches the work products that landed in the
twin and has a judge model (default `claude-opus-5`, override with
`METAFORGE_JUDGE_MODEL` or `--model`) score them against the scenario's
`definition_of_done`, attaching an advisory `judge` block per run plus a
report-level summary. Deterministic checks stay authoritative for pass/fail;
judge scores are quality signal for trend diffs.

```bash
# Judge a runs-suite report (uses ANTHROPIC_API_KEY / ant auth profile)
python3 evals/judge.py --report evals/report.json --gateway http://fidel-dev:8000

# Judge chat-suite work products (chat_brief_* scenarios carry a definition_of_done)
python3 evals/judge.py --report evals/chat_report.json --only chat_brief_project
```

Verdicts are structured-output JSON (per-phase `score`/`verdict`/`missing` +
`overall_score`), the judge model and `prompt_version` are pinned in the
report for comparability, and the call opts into server-side refusal
fallbacks so a safety decline re-runs on Anthropic's recommended fallback
model instead of losing the verdict. Runs without a `definition_of_done` or a
`project_id` are skipped, never failed.
