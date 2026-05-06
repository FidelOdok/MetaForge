# Tier-1 — end-to-end user journey happy path (HP-E2E)

Validates: MET-419 (sub-deliverable 10/10 of MET-409). This is
the story-acceptance corpus for the epic.
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario e2e-hp`

Ten happy-path scenarios that compose every other Cycle-3
sub-deliverable into full user journeys. **If a real user can't
complete these flows via Claude Code, the platform is not done —
regardless of what other tests pass.**

> **Note:** HP-E2E-08 and HP-E2E-09 depend on Phase-2 dashboard
> visualization (MET-394..397). HP-E2E-10 depends on per-call
> context (MET-387). Where dashboard visuals are not available,
> the dashboard scenarios may report BLOCKED with environment
> notes — the underlying API contract is still validated.

---

## Scenario: HP-E2E-01 — `forge init` on empty project
Validates: cycle-1 init flow
Tier: 1

### Given
- A new, empty project directory.

### When
1. Run `forge init`.

### Then
- A Project node is created in the twin.
- The gateway returns `200` on `/health`.
- KnowledgeService reports ready.

---

## Scenario: HP-E2E-02 — ingest a design memo via Claude Code MCP
Validates: MET-410, MET-346
Tier: 1

### Given
- A markdown memo in the project.

### When
1. Prompt Claude Code: *"Ingest this memo."*

### Then
- The trace shows a `knowledge.ingest` call.
- Reported `chunks_indexed >= 1`.
- The memo appears in `metaforge://knowledge/sources`.

---

## Scenario: HP-E2E-03 — search the memo via Claude Code
Validates: MET-411, MET-346
Tier: 1

### Given
- The memo from HP-E2E-02 is ingested.

### When
1. Prompt: *"What does the memo say about thermal management?"*

### Then
- The trace shows a `knowledge.search` call.
- The reply quotes the memo and includes a citation
  (`source_path` + heading).

---

## Scenario: HP-E2E-04 — generate a CAD part via cadquery
Validates: MET-414
Tier: 1

### Given
- (no specific seed)

### When
1. Prompt: *"Make a 50×30×10mm aluminum housing."*

### Then
- A STEP and a GLB are produced.
- A DesignElement node is created with material=aluminum.
- The GLB is reachable via its MinIO URL.

---

## Scenario: HP-E2E-05 — run FEA on the generated part
Validates: MET-415
Tier: 1

### Given
- The part from HP-E2E-04.

### When
1. Prompt: *"Run a stress analysis with a 10N load on the top
   face."*

### Then
- A `.frd` output is produced.
- A SimulationRun node is linked back to the DesignElement.
- The reported max stress is non-zero and physically plausible.

---

## Scenario: HP-E2E-06 — view the result in the Digital Twin viewer
Validates: dashboard digital-twin route (Phase 2)
Tier: 1

### Given
- The part + simulation from HP-E2E-04/05.

### When
1. Open `/digital-twin` in the dashboard.

### Then
- The part renders in the R3F viewer.
- It is clickable; selecting it loads the Knowledge tab
  populated with related chunks.

---

## Scenario: HP-E2E-07 — constraint validation pre-DVT
Validates: MET-413
Tier: 1

### Given
- A project with all upstream artifacts (parts, BOM, requirements)
  in place.

### When
1. Prompt: *"Validate the design before gate review."*

### Then
- The trace shows a `constraint.validate` call.
- Either a clean response (`violations=[]`) is reported as gate-
  ready, or violations are listed with severity and remediation.

---

## Scenario: HP-E2E-08 — dashboard `/knowledge` lists ingested sources
Validates: dashboard knowledge route (Phase 2)
Tier: 1

### Given
- Ingested sources from earlier scenarios.

### When
1. Open `/knowledge` in the dashboard.

### Then
- The ingestion strip lists the sources.
- The graph canvas renders nodes for the sources and their
  related entities.

---

## Scenario: HP-E2E-09 — citation viewer opens source PDF inline
Validates: dashboard citation viewer (Phase 2)
Tier: 1

### Given
- A search result in the dashboard whose hit cites a PDF.

### When
1. Click the citation.

### Then
- The PDF.js viewer opens inline.
- It scrolls to the cited chunk.
- The cited region is visually highlighted.

---

## Scenario: HP-E2E-10 — multi-project isolation
Validates: MET-387, MET-401
Tier: 1

### Given
- Two projects (A and B) with non-overlapping content.

### When
1. While in Project A's context, search and validate.
2. Switch to Project B's context, repeat.

### Then
- Each project's results contain only that project's content.
- Constraint validation in B does not surface A's violations.

---

## Acceptance

- All 10 scenarios PASS.
- This sub-deliverable closes only when the rest of MET-409 is
  done — it composes across all of them.
- On PASS → MET-409 epic closes.
- Report committed.
