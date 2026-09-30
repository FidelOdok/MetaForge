---
description: Plan a design flow for this project
---

Propose a design flow tailored to this project (FORGE-400).

Call `flow.list` first. The templates are real and the phase ids matter -- a flow tailored from a template you invented is refused.

Then `flow.propose` with the project's intent and any recorded requirements. It returns the tailored flow, each change with its rationale, and an **approval id**.

**Stop there.** The proposal is held for a person. You cannot approve it and no tool would let you -- report the changes and the approval id, say plainly that nothing runs until somebody answers, and do not poll waiting for an approval you were not given.

Once a human has approved it, `flow.start_run` with the version id starts the run. Follow it with `flow.status` or by re-reading `metaforge://flow/run/<run_id>`. A phase reading `unknown` means its state could not be read -- say unknown, never 'not started yet'.
