# run_erc

Runs Electrical Rules Check (ERC) on a KiCad schematic and returns categorised violations.

## What it does

1. Takes a schematic work_product ID and file path as input
2. Invokes the KiCad ERC tool via MCP bridge
3. Parses and categorises violations by severity (error / warning)
4. Returns a structured report with pass/fail status

## Tools Required

- `kicad.run_erc` -- KiCad Electrical Rules Check

## Input

- `work_product_id` -- ID of the schematic work_product in the Digital Twin
- `schematic_file` -- Path to the KiCad schematic file (.kicad_sch)
- `severity_filter` -- Filter violations by severity: "all", "error", or "warning" (default: "all")

## Output

- `passed` -- Whether the schematic passed ERC (no errors; warnings are acceptable)
- `total_violations` -- Total number of violations found
- `total_errors` -- Number of error-severity violations
- `total_warnings` -- Number of warning-severity violations
- `violations` -- List of ERC violations with rule_id, severity, message, sheet, component, pin, location
- `summary` -- Human-readable summary string

## Pass/Fail Logic

- **PASSED**: Zero error-severity violations (warnings are allowed)
- **FAILED**: One or more error-severity violations

## Limitations

- Phase 1 is read-only: ERC can detect problems but cannot auto-fix them
- Relies on KiCad ERC rule set; custom rules require a rule set file
- Does not perform cross-sheet net connectivity analysis beyond what KiCad ERC provides
