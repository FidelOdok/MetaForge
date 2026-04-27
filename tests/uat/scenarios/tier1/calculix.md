# Tier-1 — `calculix.*` scenarios

Validates the CalculiX FEA MCP surface from a Claude-as-real-user
perspective. Run on Cycle gates.

Every scenario assumes the MetaForge MCP server is connected and
the four `calculix.*` tools are listed in `tool/list`.

> **Note:** CalculiX runs in its container — many of the heavier
> handler calls require an `.inp` file or a mesh on disk. The
> scenarios below focus on the contract Claude actually exercises in
> a typical user flow (`validate_mesh` smoke + `extract_results`
> shape) rather than driving a full FEA solve. Full-solve UAT is
> Phase-2 scope.

---

## Scenario: tool/list reports all four calculix tools
Validates: MET-337
Tier: 1

### Given
- (preflight only)

### When
1. (no MCP call needed beyond the agent's own discovery)

### Then
- The `calculix.*` tools visible to the agent are exactly:
  `run_fea`, `run_thermal`, `validate_mesh`, `extract_results`.

---

## Scenario: validate_mesh accepts an empty / synthetic mesh ref
Validates: MET-340 error-handling contract
Tier: 1

### Given
- `calculix.validate_mesh` reachable.

### When
1. Call `calculix.validate_mesh` with an obviously-invalid mesh
   path (e.g. `"/tmp/does-not-exist.msh"`).

### Then
- The response is a tool error or `status: "failure"`.
- The error message references the missing/unreadable file.
- The MCP transport stays alive — next call works.

---

## Scenario: run_fea rejects missing required arguments cleanly
Validates: MET-340 schema enforcement
Tier: 1

### Given
- `calculix.run_fea`'s schema declares its required fields in the
  manifest (mesh, material, boundary conditions, load cases).

### When
1. Call `calculix.run_fea` with an empty arguments object `{}`.

### Then
- The response is a tool error (-32001) OR a structured failure.
- The error data names at least one missing required field.
- Subsequent calls still succeed.

---

## Scenario: extract_results on a non-existent run id fails cleanly
Validates: MET-340 error-handling contract
Tier: 1

### Given
- `calculix.extract_results` reachable.

### When
1. Call `calculix.extract_results` with `run_id: "uat-nonexistent-9z"`.

### Then
- Response is a tool error or `status: "failure"`.
- Error data does NOT leak filesystem paths from the container.
- Subsequent calls work.

---

## Scenario: run_thermal manifest is reachable
Validates: MET-337
Tier: 1

### Given
- `calculix.run_thermal` listed in `tool/list`.

### When
1. (manifest-only check; no handler call needed)

### Then
- The agent's local manifest cache for `calculix.run_thermal` has
  a non-empty `description` and an `input_schema` with at least
  one declared property.
