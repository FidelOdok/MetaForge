# check_power_budget

Check a design's power budget: verify the supply/rails can meet the aggregate load with margin.

## What it does

1. Takes the design's power sources/rails and the per-component current/power draw
2. Sums worst-case draw per rail and compares it against each rail's supply capacity
3. Applies a margin/derating target and flags any rail that is over budget
4. Returns a per-rail pass/fail budget with headroom

## Tools Required

- `power.check_budget` -- deterministic budget arithmetic over the supplied rail/load data (FORGE-544)

## Input

- `rails` -- each: `name`, `voltage_v`, `source_kind` (`supply`, `ldo`, `switching`), `rated_current_ma`, `input_rail` (regulators), `efficiency` (switching), `quiescent_ma` (ldo), `source`
- `loads` -- each: `name`, `rail`, `current_ma` or `power_mw`, `source`. A load with neither is unknown
- `derating` -- allowed share of each rated output, from the user (0.8 = 80 %)

## Output

- per rail: `load_ma` (own loads plus regulator input current fed from it), `allowed_ma`, `headroom_ma`, `headroom_pct`, `status` (`pass`, `fail`, `not_established`), `unknowns`, `notes`
- `verdict`, `passed`, `worst_rail`, `source_power_mw`, `summary`

## Limitations

- Only as accurate as the per-component draw figures supplied
- Static worst-case sum; no transient/inrush or thermal-derating modeling
