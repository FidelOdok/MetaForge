# Design-flow scenario evals

The eval flywheel for the gated design-flow harness: run reference
hardware/robotics projects through the **real** harness, capture what actually
happened, and let the gaps prioritise development.

```
   scenarios ──► experimentation ──► data ──► insights ──► development
      ▲                                                          │
      └──────────────────────────────────────────────────────────┘
```

This directory is **step 1–2** (scenarios + experimentation → structured data).
Rubric/quality scoring (step 3) consumes the `definition_of_done` carried in the
report.

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
