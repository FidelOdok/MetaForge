# MetaForge

This project is tracked in a MetaForge digital twin. Read before you build, and record what you decide.

## Start here

Call `project.list`, pick the project, then `session.start` with its `project_id` so your work is attributed to it. Read `metaforge://twin/brief/<project_id>` before designing anything — it lists what already exists, newest first.

## What the twin expects

- Give every CAD part a meaningful name. Never `Part_1`.
- Record decisions with `twin.record_decision`, including the alternatives you rejected.
- Pin evidence to the revision it came from with `twin.record_evidence`. A result that outlives its design is stale, not supporting.

## Reading the answers honestly

- A requirement with status `no_data` has no evidence at all. That is a gap, not a pass.
- A write may be **held for approval**. That is the system working: tell the user it is waiting in the dashboard rather than retrying.
- If `tools/list` or `resources/list` returns `_meta.unavailableAdapters`, some capability is missing because a container is down. Say which, rather than describing what is left as if it were everything.
