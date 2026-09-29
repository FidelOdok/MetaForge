---
description: Review a maturity gate
---

Review whether the active project can be promoted.

Read `metaforge://twin/requirements/<project_id>` and report each required claim's status. `uncertain`, `stale` and `no_data` all block; only an approved waiver naming that requirement overrides a `fail`.

`twin.attempt_promotion` refuses rather than warns, and it needs a named human in `decided_by`. Do not supply one on the user's behalf.
