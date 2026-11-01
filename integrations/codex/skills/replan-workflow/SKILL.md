---
name: replan-workflow
description: Change a running design flow without starting again in MetaForge. Use when the user asks to change a running design flow without starting again.
---

# Change a running design flow without starting again

Apply new information to a running design flow (FORGE-539).

Read the run with `flow.status` and note its `flowContentHash`. Decide what the new information changes: phases whose results it makes wrong go in `invalidate` (a heavier payload invalidates the mechanical design, not the electronics); a different structure goes in `operations` (the same closed set as `flow.propose`). Ask the user for any value you were not given rather than assuming it.

Call `flow.patch` with `action: propose`, the run id, the hash as `expected_content_hash` and a `reason`. It returns what re-runs and what is kept, and an approval id. **Stop there**: the patch is held for a person, you cannot approve it and no tool would let you. A refusal that says the patch is stale means the run moved on; read it again and write a new patch. Once the user says it was approved, call `flow.patch` with `action: apply` and the `version_id`; it takes effect at the run's next gate.
