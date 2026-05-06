# Tier-1 — brownfield import happy path (HP-BROWN)

Validates: MET-418 (sub-deliverable 9/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario brownfield-hp`

Ten happy-path scenarios for brownfield project import (ADR-009).
Every scenario assumes the `forge import` CLI is installed and
the reconciliation skills (MET-348..351) are deployed.

> **Note:** Brownfield import depends on cycle-3 deliverables
> that may not be wired into every dev environment yet. If the
> CLI or a reconciliation skill is missing, the scenario reports
> BLOCKED, not FAIL.

---

## Scenario: HP-BROWN-01 — small project import (10 files)
Validates: MET-349 (forge import CLI)
Tier: 1

### Given
- A fixture directory `tests/fixtures/sample-project/` with 10
  mixed files (markdown, KiCad, BOM CSV, PDF).

### When
1. Run `forge import tests/fixtures/sample-project/`.

### Then
- All 10 files are dispatched. The CLI reports each file's
  outcome (success / skipped / failed).
- Exit code is 0 when no per-file failures occur.

---

## Scenario: HP-BROWN-02 — KiCad schematic → DesignElement nodes
Validates: MET-349, KiCad ingest skill
Tier: 1

### Given
- The fixture's KiCad files (`.kicad_sch`, `.kicad_pcb`).

### When
1. Inspect the twin after import.

### Then
- DesignElement nodes are created for the schematic / PCB.
- Properties (sheet name, ref designators where extractable)
  are populated.

---

## Scenario: HP-BROWN-03 — KiCad BOM CSV → BOMItem rows
Validates: BOM ingest skill
Tier: 1

### Given
- The fixture's BOM CSV.

### When
1. Query `twin.find_by_property` with `label="BOMItem"`,
   filtered to the imported project.

### Then
- BOMItem nodes match the BOM rows.
- MPNs are preserved verbatim. Quantity and reference designator
  fields are populated where the CSV had them.

---

## Scenario: HP-BROWN-04 — FreeCAD .FCStd → DesignElement + STEP→GLB
Validates: MET-349, FreeCAD ingest skill
Tier: 1

### Given
- A FreeCAD `.FCStd` file in the fixture.

### When
1. Inspect imported DesignElement nodes for the `.FCStd`.

### Then
- A DesignElement node is created.
- A GLB rendering of the geometry exists in MinIO and renders
  in the dashboard viewer.

---

## Scenario: HP-BROWN-05 — datasheet PDFs → KnowledgeSource nodes
Validates: MET-349 + knowledge ingest
Tier: 1

### Given
- The fixture's PDF datasheets.

### When
1. Read `metaforge://knowledge/sources` after import.

### Then
- A `KnowledgeSource` row exists per PDF, with
  `status="indexed"` and `fragmentCount > 0`.

---

## Scenario: HP-BROWN-06 — SRS PDF → Requirement proposals
Validates: MET-351 (extract_requirements skill)
Tier: 1

### Given
- A short SRS PDF in the fixture.

### When
1. Read the proposal queue (`/approvals` route or proposal API)
   after import.

### Then
- Requirement proposals are queued with non-empty `citation`
  fields (page + heading) and a `confidence` score in [0, 1].

---

## Scenario: HP-BROWN-07 — test reports → Evidence proposals
Validates: reconciliation backfill
Tier: 1

### Given
- A test-report fixture (CSV or markdown) describing past test
  results.

### When
1. Read the proposal queue after import.

### Then
- TestExecution + Evidence proposals are queued.
- Each proposal references the source report file via
  `sourceRef` / citation.

---

## Scenario: HP-BROWN-08 — idempotent re-import (no duplicates)
Validates: MET-349, MET-348 (provenance schema)
Tier: 1

### Given
- A project already imported once.

### When
1. Run `forge import` against the same directory again.

### Then
- No new nodes are created (per-file dedup confirmed via
  before/after counts).
- The CLI reports "already imported" or equivalent for each file.

---

## Scenario: HP-BROWN-09 — provenance fields populated correctly
Validates: MET-348
Tier: 1

### Given
- A project imported with mixed direct-import and
  reconciliation-promoted nodes.

### When
1. Sample one node from each origin.

### Then
- Direct-imported nodes carry `provenance="imported"` with
  `sourceRef` pointing at the original file.
- Reconciliation-promoted nodes (after acceptance) carry
  `provenance="inferred"` plus citation back to the source
  doc/section.

---

## Scenario: HP-BROWN-10 — proposal queue review → accept → mutation
Validates: MET-350 (proposal queue)
Tier: 1

### Given
- A proposal queue with at least one high-confidence proposal.

### When
1. Walk the proposal queue and accept the proposal (via UI or
   API).

### Then
- The accepted proposal becomes a first-class twin node.
- The originating citation is preserved as `sourceRef` on the
  resulting node.
- Counts after acceptance reflect the new node (twin growth = 1).

---

## Acceptance

- All 10 scenarios PASS.
- Report committed.
