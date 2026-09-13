# analyze_hazards

Logs hazards for a system and persists them as a `HAZARD_ANALYSIS` work
product via `twin.commit_hazard_analysis`.

## What it does

1. Takes a system name and a list of hazards (hazard, cause, effect,
   severity 1-5, likelihood 1-5, mitigation).
2. Calls `twin.commit_hazard_analysis`, which scores each hazard
   (severity x likelihood), sorts by risk, and persists a markdown log.
3. Classifies the overall risk level from the highest score: >=20
   critical, >=12 high, >=6 medium, else low.

## Input

`project_id`, `system_name`, `hazards` (each: hazard, cause, effect,
severity, likelihood, mitigation).

## Output

`node_id`, `hazard_count`, `highest_risk_score`, `overall_risk_level`,
`unmitigated_count`.

## Limitations

Not idempotent -- re-running creates a new node each time (no dedup like
`twin.record_decision`). Severity/likelihood are self-reported inputs, not
derived from simulation or FEA results.
