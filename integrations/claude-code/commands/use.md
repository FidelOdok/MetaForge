---
description: Pick the project to work in for this session
---

Set the active MetaForge project.

Call `project.list` to show the projects on this gateway, ask which one if the user has not said, then call `session.start` with that `project_id` so everything recorded afterwards is attributed to it.

Then read `metaforge://twin/brief/<project_id>` and summarise where the project stands — newest work first. Do not restate the whole brief.
