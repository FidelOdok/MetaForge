---
name: use-workflow
description: Pick the project to work in for this session in MetaForge. Use when the user asks to pick the project to work in for this session.
---

# Pick the project to work in for this session

Set the active MetaForge project.

Call `project.list` to show the projects on this gateway, ask which one if the user has not said, then call `session.start` with that `project_id` so everything recorded afterwards is attributed to it.

Read `project_scope_bound` in the reply. `false` means the scope did not stick for this client: pass `project_id` explicitly on every later call that takes one, and say so once — otherwise the next few calls land on the wrong project and nothing reports it.

`project.open` returns the brief inline. Summarise where the project stands — newest work first — and do not restate the whole brief. If no brief came back, read `metaforge://twin/brief/<project_id>`; if that is not available either, say so rather than describing the project from its name.
