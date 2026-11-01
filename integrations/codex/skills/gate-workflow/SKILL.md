---
name: gate-workflow
description: Review a maturity gate in MetaForge. Use when the user asks to review a maturity gate.
---

# Review a maturity gate

Review whether the active project can be promoted.

Read `metaforge://twin/requirements/<project_id>` and report each required claim's status. `uncertain`, `stale` and `no_data` all block; only an approved waiver naming that requirement overrides a `fail`.

If `twin.attempt_promotion` is not in your tool list, this connection cannot promote: report the statuses and say so (FORGE-533). When it is, it refuses rather than warns. Do not pass `decided_by` -- it is not an argument and supplying it is an error. The call is always held for a person, and whoever approves it is recorded as the deciding authority.
