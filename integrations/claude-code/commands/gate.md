---
description: Review a maturity gate
---

Review whether the active project can be promoted.

Read `metaforge://twin/requirements/<project_id>` and report each required claim's status. `uncertain`, `stale` and `no_data` all block; only an approved waiver naming that requirement overrides a `fail`.

`twin.attempt_promotion` refuses rather than warns. Do not pass `decided_by` -- it is not an argument and supplying it is an error. The call is always held for a person, and whoever approves it is recorded as the deciding authority.
