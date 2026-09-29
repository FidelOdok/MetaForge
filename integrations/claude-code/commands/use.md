---
description: Pick the project to work in for this session
---

Set the active MetaForge project.

Call `project.list` to show the projects on this gateway, ask which one if the user has not said, then call `session.start` with that `project_id` so everything recorded afterwards is attributed to it.

Read `project_scope_bound` in the reply. `false` means the scope did not stick for this client: pass `project_id` explicitly on every later call that takes one, and say so once — otherwise the next few calls land on the wrong project and nothing reports it.

Then read `metaforge://twin/brief/<project_id>` and summarise where the project stands — newest work first. Do not restate the whole brief.
