# MetaForge

This project is tracked in a MetaForge digital twin. Read before you build, and record what you decide.

## Start here

Call `project.list`, pick the project, then `session.start` with its `project_id` so your work is attributed to it. Read `metaforge://twin/brief/<project_id>` before designing anything — it lists what already exists, newest first.

## What the twin expects

- Give every CAD part a meaningful name. Never `Part_1`.
- Record decisions with `twin.record_decision`, including the alternatives you rejected.
- Pin evidence to the revision it came from: record a result with `twin.record_document` (`document_type: simulation_result`, with `analysed_geometry_node_id` and its revision), and with `twin.record_evidence` when that tool is in your list. A result that outlives its design is stale, not supporting.

## Taking a product from intent to a verified design

Follow the bundled `intent-to-verified-design` skill. In short:

- Ask the user for values you were not given (loads, materials, manufacturing route, maturity). Unknown is a valid answer; a guess is not.
- Only create a project when the user asked for one. A flow can be proposed without one.
- Propose a flow with `flow.propose`, passing `template` and `operations` together. Never `template` alone: that means 'use it unchanged'. Then stop: a person approves it, and there is no tool that lets you.

## Reading the answers honestly

- A requirement with status `no_data` has no evidence at all. That is a gap, not a pass.
- A write may be **held for approval**. That is the system working: tell the user it is waiting in the dashboard rather than retrying. If an approval times out or is refused, ask the user before trying again.
- A design-run phase reading `unknown` could not be read. Say unknown, never 'not started'.
- If `tools/list` or `resources/list` returns `_meta.unavailableAdapters`, some capability is missing because a container is down. Say which, rather than describing what is left as if it were everything. A shorter tool list can also be the connection's profile: check `health.check` before concluding anything is missing.
