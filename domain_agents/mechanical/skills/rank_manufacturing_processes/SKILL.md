# rank_manufacturing_processes

Ranks candidate manufacturing processes for a part before it is designed, so the chosen process can constrain the design.

## What it does

1. Takes the part's needs (quantity, material class, envelope, minimum feature, tightest tolerance) and candidate processes with their sourced capabilities
2. Scores each candidate on envelope, minimum feature, tolerance, material and availability on the route: pass, fail or unknown
3. Works out cost at quantity (setup amortised plus unit cost) when all three are known
4. Orders the candidates: fits, then incomplete, then does not fit; cheaper first
5. Recommends the best candidate that fits (or the best incomplete one); the person picks at the route gate

## Tools Required

None -- pure computation. Capability values must be sourced first (source-engineering-values). For a machine in the machine library, library.machine_limits returns a ready candidate with its sources.

## Input

- `quantity`, `material_class`, `part_envelope_mm`, `min_feature_mm`, `tightest_tolerance_mm` -- the part's needs; any may be missing
- `candidates` -- processes with `route`, `available`, `max_envelope_mm`, `min_feature_mm`, `achievable_tolerance_mm`, `materials`, `setup_cost`, `unit_cost`, `lead_time_days`, `source_refs`

## Output

- `ranked` -- per candidate: verdict (fits / incomplete / does_not_fit), criteria, cost at quantity, reasons, sources
- `recommended` -- best candidate, or none when nothing fits
- `unknown_inputs` -- part needs that were not given

## Rules

- A capability that was not sourced is never assumed: its criterion reads unknown
- The ranking is a recommendation; the decision is the person's at the route gate

## Limitations

- No built-in process capability data; every value comes from the caller
- Cost is setup plus unit cost only (no finishing, logistics or scrap)
