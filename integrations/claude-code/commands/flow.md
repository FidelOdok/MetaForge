---
description: Plan a design flow for this project
---

Propose a design flow tailored to this project (FORGE-400).

Call `flow.list` first. The templates are real and the phase ids matter -- a flow tailored from a template you invented is refused.

Ask the user for the three inputs a proposal needs, rather than guessing them: the manufacturing route (`in_house`, `vendor` or `undecided`, plus processes, machines and stock when in-house), the target maturity (`concept`, `sim_validated`, `physically_validated` or `released`), and the loads with numbers or the word `unknown`. If `flow.propose` still returns `needs_input`, put its questions to the user verbatim.

Then `flow.propose` with the intent, those inputs and any recorded requirements, and tailor the template yourself (FORGE-533): pass `template` **and** `operations` together -- `drop_phase`, `add_deliverable`, `set_disciplines`, `set_model`, `declare_items`, each on a phase id from `flow.list` with a rationale that follows from what the user stated. Only use artifact types `flow.list` already shows. Send `operations: []` when the template is right as it is, and say so. Never send `template` without `operations`: that means 'use it unchanged', with no tailoring at all. A refusal names what failed; fix that one thing and try again, at most twice. It returns the tailored flow, each change with its rationale, and an **approval id**.

**Stop there.** The proposal is held for a person. You cannot approve it and no tool would let you -- report the changes and the approval id, say plainly that nothing runs until somebody answers, and do not poll waiting for an approval you were not given.

Once a human has approved it, `flow.start_run` with the version id starts the run. Follow it with `flow.status` or by re-reading `metaforge://flow/run/<run_id>`. A phase reading `unknown` means its state could not be read -- say unknown, never 'not started yet'.
