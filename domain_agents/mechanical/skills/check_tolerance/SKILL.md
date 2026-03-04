# check_tolerance

Analyzes dimensional tolerances against manufacturing process capabilities and DFM rules.

## What it does

1. Takes a list of tolerance specifications and a manufacturing process definition
2. Computes process capability index (Cp) for each tolerance
3. Checks DFM rules: minimum feature size, aspect ratio, achievable tolerance
4. Optionally performs RSS tolerance stack-up analysis
5. Returns a compliance report with pass/warning/fail status and flagged violations

## Tools Required

None -- this is a pure computation skill. No MCP tool invocation.

## Input

- `artifact_id` -- ID of the artifact in the Digital Twin
- `tolerances` -- List of tolerance specs (dimension_id, feature_name, nominal_value, upper/lower tolerance)
- `manufacturing_process` -- Process capabilities (process_type, achievable_tolerance, min_feature_size, etc.)
- `material` -- Material identifier (default: aluminum_6061)
- `check_stack_up` -- Whether to perform RSS tolerance stack-up analysis

## Output

- `overall_status` -- "pass", "marginal", or "fail"
- `total_dimensions_checked` -- Count of dimensions analyzed
- `passed` / `warnings` / `failures` -- Per-status counts
- `results` -- Per-dimension results with Cp and status
- `violations` -- List of DFM violations with severity and recommendations
- `summary` -- Human-readable summary string

## DFM Rules

- **Too tight tolerance**: Tolerance band < process achievable tolerance (error)
- **Below min feature**: Nominal value < process minimum feature size (error)
- **Aspect ratio exceeded**: Estimated aspect ratio > process limit (warning)
- **Capability index**: Cp < 1.0 = fail, 1.0-1.33 = warning, >= 1.33 = pass

## Limitations

- Aspect ratio check uses nominal_value / tolerance_range as a proxy
- Stack-up analysis uses RSS method with a 75% worst-case threshold heuristic
- Does not account for GD&T form/profile tolerances
- Does not consider thermal expansion or environmental factors
