# check_power_budget

Check a design's power budget: verify the supply/rails can meet the aggregate load with margin.

## What it does

1. Takes the design's power sources/rails and the per-component current/power draw
2. Sums worst-case draw per rail and compares it against each rail's supply capacity
3. Applies a margin/derating target and flags any rail that is over budget
4. Returns a per-rail pass/fail budget with headroom

## Tools Required

- (none) -- deterministic budget arithmetic over the supplied rail/load data

## Input

- power sources / rails (voltage, capacity)
- per-component load (rail, current or power, duty)
- target margin / derating factor

## Output

- per-rail budget: total draw vs capacity, headroom, and pass/fail
- overall `passed` and the worst-case rail

## Limitations

- Only as accurate as the per-component draw figures supplied
- Static worst-case sum; no transient/inrush or thermal-derating modeling
