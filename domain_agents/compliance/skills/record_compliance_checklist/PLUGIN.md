---
description: Build a regulatory compliance checklist for a project's target markets (UKCA, CE, FCC, PSTI) from MetaForge's built-in regime catalogue and save it in the twin as a reviewable work product linked to the project. Use when the user asks to save, record, attach or track a compliance or certification checklist for a project, or wants the checklist to show up on the project's work products for review. To only see the checklist without saving it, use generate_checklist.
---

# record_compliance_checklist

The persisted version of `generate_checklist`: the same deduplicated
checklist (requirement, standard, evidence type, evidence status per item),
written to the digital twin so a reviewer can see it on the project and so
later work can fill in the evidence against it.

## When to use it

- "Save a UKCA and CE checklist on the drone project."
- "Record what we need for FCC so the team can track it."
- "Attach the compliance checklist to the project before the gate review."

Not for:

- Only looking at the checklist: use the `generate_checklist` skill, which
  writes nothing.
- Safety hazards of the design: use the `analyze_hazards` skill.
- Recording that a piece of evidence (a lab report) now exists: no MCP tool
  updates a checklist item's evidence status. See Limits.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project | `project.open`, `project.get` | every profile |
| Look for an earlier checklist | `metaforge://twin/brief/<project_id>` (resource), `twin.find_by_property` | every profile |
| Record (preferred) | `twin.commit_compliance_checklist` | not served by the dev MCP server; see below |
| Record (fallback) | `twin.record_document` | `core` |
| Record scope decisions | `twin.record_decision` | every profile |

The handler builds the checklist from a catalogue that ships with MetaForge
(`domain_agents/compliance/regimes/`). No MCP tool computes it and no MCP
resource exposes the catalogue. Build it from the catalogue table in the
`generate_checklist` skill, which is a copy of the same files; load that
skill if you do not already have it.

`twin.commit_compliance_checklist` is registered only when the server is
wired with a compliance-checklist recorder. The gateway's in-process harness
has it; the dev MCP sidecar does not, and it is hidden from chat tool
lists. Expect to use the fallback.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| Project | The checklist is linked to it; required here | `Drone FC`, or its UUID |
| Target markets | Selects regimes; order decides which regime keeps a shared standard | `UKCA, CE` |
| Product category | Stored with the checklist; the catalogue does not vary by it | `consumer_electronics` |
| A name, if the user wants one | The handler names it `<project_id> Compliance Checklist`; a readable name is better | `Drone FC UKCA+CE checklist` |

Only `UKCA`, `CE`, `FCC` and `PSTI` exist. Ask before adding `PSTI`; it
applies to connectable products.

## Procedure

### 1. Set up

1. Call `health.check` and note the active profile.
2. Call `project.open` with the user's words as `query`. Several matches
   come back as an error: ask which one, never pick.
3. Read `metaforge://twin/brief/<project_id>`. If a compliance checklist is
   already listed for this project, show it and ask whether to record a new
   one. Recording is not idempotent: every call makes a new node, with no
   deduplication against the old one.

### 2. Build the checklist

Follow the `generate_checklist` procedure exactly:

- Ask which product features apply (`radio`, `mains_powered`, `battery`,
  `connected`, `body_worn`) and exclude the conditional items the product
  does not need, listing each with its missing feature. If the user will
  not say, keep every item and list the conditional ones as questions.
- Walk the markets in alphabetical order, each market's rows top to bottom;
  fold a row whose exact standard string was already taken into that row.
- Every item's `evidence_status` is `MISSING` unless the user confirmed
  evidence for it.
- `total_items` is the count; `coverage_percent` is the share of items not
  `MISSING`.

Show the checklist and the excluded items to the user. Do not drop rows on
your own judgement beyond the stated features. If the user rules items or a
regime out, record that with `twin.record_decision` (`title`, `rationale`,
`alternatives`).

### 3. Record it

Ask the user before writing. Then use the first path your list allows.

**a. If `twin.commit_compliance_checklist` is in your tool list**, call it
with:

- `name`: the agreed name
- `target_markets`: e.g. `["UKCA", "CE"]`
- `items`: one object per row with `id`, `regime`, `category`,
  `requirement`, `standard`, `evidence_type` (`TEST_REPORT`, `DECLARATION`,
  `CERTIFICATE`, `TECHNICAL_FILE`, `RISK_ASSESSMENT`) and `evidence_status`
  (`MISSING` unless evidence was confirmed)
- `coverage_percent`: as computed
- `project_id`

It returns `node_id`, `total_items`, `coverage_percent` and
`project_linked`. Check `total_items` matches yours and `project_linked` is
true.

**b. If it is not**, call `twin.record_document` (profile `core`) with:

- `document_type: "documentation"`
- `name`: the agreed name
- `content`: markdown, a heading `# Compliance Checklist: <markets>`, a line
  `Coverage: <coverage_percent>%`, then a table with columns Regime, Category, ID,
  Requirement, Standard, Evidence, Status
- `metadata`: `target_markets`, `product_category`, `total_items`,
  `coverage_percent`, and `items` (the rows)
- `project_id`

Tell the user it was stored as a documentation work product, not as a typed
COMPLIANCE_CHECKLIST work product, because this connection does not serve
that recorder.

If neither tool is available, give the checklist in your reply, say nothing
was recorded, and name the profile that would allow it (`core`).

### 4. Report

- What was recorded: `node_id`, name, which path (typed or documentation).
- Markets, features, `total_items`, `coverage_percent`.
- Shared standards that were folded and which item kept them.
- Excluded items with the missing feature, or the conditional items as
  open questions if features were not stated.
- That evidence status stays `MISSING` until a person attaches evidence,
  and that no MCP tool on this connection updates it.

## Checks before you report

- [ ] The project was resolved, not guessed
- [ ] You checked for an existing checklist before writing a new one
- [ ] Markets are only the four supported regimes
- [ ] Deduplication used exact standard strings, markets in alphabetical order
- [ ] Rows are `MISSING` unless evidence was confirmed; coverage matches
- [ ] You said which recording path was used, with the returned node id

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `twin.commit_compliance_checklist` not in your list | Server not wired with that recorder (normal on the dev sidecar) | Use path 3b; tell the user. |
| `twin.record_document` not in your list | Connection is not on `core` | Ask the user to connect with `?profile=core`, or report unrecorded. |
| "'items' must be a non-empty array" | Every row was removed | A checklist needs at least one item; re-check scope with the user. |
| `project_linked: false` | Project id was wrong or not visible | Re-resolve with `project.get`; tell the user the node exists but is unlinked. |
| A write is held / `approval_required` | Writes need a person on this connection | Tell the user where it waits; do not retry or reword it. |
| A second checklist appeared | The call was repeated | Not idempotent; tell the user which node is current. Do not delete nodes yourself. |
| Market not in the catalogue | Only four regimes exist | Say so; do not invent its items. |
| `-32001` naming an adapter | That adapter's container is down | No adapter is needed here; say so if a lookup failed and continue. |

## Limits

- Same catalogue limits as `generate_checklist`: four regimes, fixed items,
  five product features, exact-string deduplication.
- Not idempotent: every recording is a new node.
- The checklist is a snapshot. No MCP tool links a test report or
  declaration to a checklist item or advances its evidence status, so the
  recorded coverage only reflects evidence known when it was recorded.
- A checklist is a completeness aid, not a conformity verdict.
