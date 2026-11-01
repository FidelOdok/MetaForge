---
description: Log the hazards of a system or subsystem, score each as severity x likelihood, classify the overall risk and record the hazard log in the MetaForge twin. Use when the user asks for a hazard analysis, risk assessment, FMEA-style hazard log, ISO 12100 or ISO 14971 style risk table, "what could go wrong with this", or when a design-flow gate reports that no risks are recorded or that a critical risk is unmitigated.
---

# analyze_hazards

Produce a reviewable hazard log for one system: what can go wrong, what
causes it, what happens if it is not mitigated, how bad and how likely it is,
and what mitigation is in place. Each hazard gets a risk score (severity x
likelihood, 1 to 25) and the log gets an overall risk level taken from its
worst hazard. The ratings are the user's engineering judgement, not yours.

## When to use it

- "Do a hazard analysis for the battery pack."
- "Build a risk table for the arm: pinch points, overheating, falls."
- "What are the safety hazards of this enclosure, and are they mitigated?"
- A gate check reads "no 'risk' entities recorded for this project yet" or
  "Risk mitigated: ... FAIL".

Not for:

- Regulatory market requirements (UKCA, CE, FCC, PSTI): use the
  `generate_checklist` or `record_compliance_checklist` skill.
- Supply-chain risk on BOM parts: use the `score_bom_risk` skill.
- Proving a mitigation works (a stress margin, a thermal limit): that is
  verification evidence, produced by the analysis skills such as `run_fea`.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project | `project.open`, `project.get` | every profile |
| Read what exists | `metaforge://twin/brief/<project_id>`, `metaforge://twin/risks/<project_id>` (resources) | every profile |
| Look up a part or document | `twin.get_node`, `twin.find_by_property`, `knowledge.search` | every profile |
| Record the hazard log (preferred) | `twin.commit_hazard_analysis` | not served by the dev MCP server; see below |
| Record each hazard as a gate-visible risk | `twin.record_engineering_entity` | full tool set only (a connection with no profile) |
| Record the log as a document (fallback) | `twin.record_document` | `core` |
| Record a judgement call | `twin.record_decision` | every profile |
| See what the gates think is broken | `twin.constraint_violations` | every profile |

`twin.commit_hazard_analysis` is registered only when the server is wired
with a hazard-analysis recorder. The gateway's in-process harness has it;
the dev MCP sidecar does not, and the tool is also hidden from chat tool
lists. Expect it to be missing from your list and use the fallback.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| Project | The log is linked to it | `Wall Shelf`, or its UUID |
| System name | Names the log; one log per system or subsystem | `Battery pack`, `Shoulder joint` |
| Each hazard | What can go wrong, in one line | "Cell thermal runaway" |
| Its cause | What triggers it | "Overcharge from a failed BMS" |
| Its effect | What happens if it is not mitigated | "Fire inside the enclosure" |
| Severity, 1 to 5 | 1 negligible ... 5 catastrophic | `5` |
| Likelihood, 1 to 5 | 1 improbable ... 5 frequent | `2` |
| Mitigation | What is in place now; empty means none | "BMS with independent overvoltage cutoff" |

You may propose candidate hazards by reading the design (the brief, the
BOM, CAD part names, the PRD). Present them as a draft table and ask the
user to confirm each row and to give its severity and likelihood. Never
assign severity or likelihood yourself, and never write "mitigated" for a
measure the user has not told you exists. If the user cannot rate a hazard
yet, leave it out of the scored log and list it as "not yet assessed".

## Procedure

### 1. Set up

1. Call `health.check`. Note the active profile; it decides which recording
   path below you can use.
2. Call `project.open` with the user's words as `query`. Several matches
   come back as an error listing them: ask which one, never pick.
3. Read `metaforge://twin/risks/<project_id>`. If risks are already
   recorded for this system, show them and ask whether this is a revision
   or a separate system. Do not silently duplicate them.

### 2. Build the hazard table with the user

1. Draft rows (hazard, cause, effect) from what you know of the design.
2. Ask the user to confirm, edit or delete each row and to supply severity,
   likelihood and mitigation. Keep their wording.
3. Check every row: hazard, cause and effect are non-empty; severity and
   likelihood are whole numbers from 1 to 5. Reject a 0, a 6 or a decimal
   and ask again.

### 3. Score it

For each hazard, `risk_score = severity x likelihood`. Classify it:

| Score | Level |
|---|---|
| 20 to 25 | `critical` |
| 12 to 19 | `high` |
| 6 to 11 | `medium` |
| 1 to 5 | `low` |

Then:

- `highest_risk_score` is the largest score in the log.
- `overall_risk_level` is the level of that highest score.
- `unmitigated_count` is the number of rows whose mitigation is empty.
- Sort the rows by score, highest first.

These are the same thresholds the server and the gate use. Show your
arithmetic in the table so a reviewer can check it.

### 4. Record the hazard log

Ask the user before writing. Then use the first path your tool list allows.

**a. If `twin.commit_hazard_analysis` is in your tool list**, call it with:

- `name`: `<system name> Hazard Analysis`
- `system_name`: the system name
- `hazards`: the rows, each `{hazard, cause, effect, severity, likelihood, mitigation}`
- `project_id`

The server computes the scores itself and returns `node_id`,
`hazard_count`, `highest_risk_score` and `unmitigated_count`. Compare them
with yours; a mismatch means a row was mistyped. Each call creates a new
node, so do not call it twice for the same log.

**b. If it is not**, call `twin.record_document` (profile `core`) with:

- `document_type: "documentation"`
- `name`: `<system name> Hazard Analysis`
- `content`: markdown with a heading `# Hazard Analysis: <system name>` and
  a table with columns Hazard, Cause, Effect, Severity, Likelihood, Risk
  Score, Risk Level, Mitigation (write `(none)` for an empty mitigation),
  sorted by score
- `metadata`: `system_name`, `hazard_count`, `highest_risk_score`,
  `overall_risk_level`, `unmitigated_count`, and `hazards` (the rows)
- `project_id`

Tell the user this was stored as a documentation work product, not as a
typed HAZARD_ANALYSIS work product, because this connection does not serve
the hazard-analysis recorder.

If neither tool is available, give the user the table in your reply, say
plainly that nothing was recorded, and name the profile that would allow it.

### 5. Make the risks visible to the gate

The design-flow risk gate (G3, Preliminary Feasibility) does not read the
hazard log. It reads `risk` entities: a risk scored `critical` with no
mitigation fails the gate, an unscored one is "not evaluated", and a
project with none recorded is "not evaluated" too.

If `twin.record_engineering_entity` is in your tool list and the user
agrees, record each hazard as one entity:

- `entity_type: "risk"`
- `title`: a short stable label, e.g. `Cell thermal runaway`
- `statement`: hazard, cause and effect in one sentence
- `extra`: `{"severity": <1-5>, "likelihood": <1-5>, "mitigation": "<text or empty>"}`
- `project_id`

Use the key `likelihood`. The tool's own description mentions
`probability`, but the gate reads `likelihood` and treats a risk without it
as not assessed. If the tool is not in your list, tell the user the hazards
were recorded as a document only and the risk gate will still show them as
missing.

### 6. Report

- The table, sorted by score, with each row's level.
- `overall_risk_level`, `highest_risk_score`, `unmitigated_count`.
- Every `critical` or `high` hazard with no mitigation, named explicitly.
- What was recorded, with the node ids the server returned, and what was not.
- Hazards the user could not yet rate, as open questions.

If the user made a judgement a reviewer could dispute (accepting a high
risk, rating a likelihood low because of a usage assumption), record it with
`twin.record_decision`: `title`, `rationale`, and the `alternatives` they
considered.

## Checks before you report

- [ ] Every severity and likelihood came from the user, not from you
- [ ] Every rating is an integer from 1 to 5
- [ ] Scores and levels match the threshold table
- [ ] Rows with no mitigation are counted as unmitigated, not hidden
- [ ] Existing risks for this system were checked before writing
- [ ] You stated which recording path was used and what was not recorded

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `twin.commit_hazard_analysis` not in your list | The server is not wired with that recorder (normal on the dev sidecar) | Use path 4b; tell the user. |
| `twin.record_document` not in your list | Connection is not on the `core` profile | Ask the user to connect with `?profile=core`, or give them the table unrecorded. |
| `twin.record_engineering_entity` not in your list | Served only on the full tool set | Say the risks are not gate-visible on this connection. |
| "'hazards' must be a non-empty array" | Every row was removed or unrated | Ask the user for at least one rated hazard. |
| Schema error on severity or likelihood | Value outside 1 to 5 or not an integer | Ask the user to correct that row. |
| A write comes back held / `approval_required` | Writes need a person on this connection | Tell the user where it waits; do not retry or reword it. |
| `-32001` naming an adapter | That adapter's container is down | Not needed for this skill; if a lookup you wanted fails, say so and continue. |
| Several projects match | Ambiguous name | Ask the user which one. |

## Limits

- Severity and likelihood are self-reported judgements. Nothing here derives
  them from FEA, thermal or test results.
- A plain 5 x 5 matrix only: no detectability term (not an FMEA RPN), no
  exposure or avoidance factors.
- Not idempotent. Each recording creates a new node; there is no
  deduplication against an earlier log for the same system.
- Mitigation is free text. Whether a mitigation actually works is a
  verification question for other skills.
