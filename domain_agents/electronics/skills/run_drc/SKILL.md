# run_drc

Runs Design Rules Check (DRC) on a KiCad PCB layout and returns categorised violations.

## What it does

1. Takes a PCB work_product ID and file path as input
2. Invokes the KiCad DRC tool via MCP bridge
3. Parses and categorises violations by severity (error / warning)
4. Returns a structured report with pass/fail status

## Tools Required

- `kicad.run_drc` -- KiCad Design Rules Check

## Input

- `work_product_id` -- ID of the PCB work_product in the Digital Twin
- `pcb_file` -- Path to the KiCad PCB file (.kicad_pcb)
- `severity_filter` -- Filter violations by severity: "all", "error", or "warning" (default: "all")

## Output

- `passed` -- Whether the PCB passed DRC (no errors; warnings are acceptable)
- `total_violations` -- Total number of violations found
- `total_errors` -- Number of error-severity violations
- `total_warnings` -- Number of warning-severity violations
- `violations` -- List of DRC violations with rule_id, severity, message, layer, location, items
- `summary` -- Human-readable summary string

## Pass/Fail Logic

- **PASSED**: Zero error-severity violations (warnings are allowed)
- **FAILED**: One or more error-severity violations

## Limitations

- Phase 1 is read-only: DRC can detect problems but cannot auto-fix them
- Relies on KiCad DRC rule set; custom rules require a rule set file
- Does not perform impedance-controlled trace validation beyond standard DRC rules
