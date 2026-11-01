---
name: verify-workflow
description: Check whether a design run achieved what was asked in MetaForge. Use when the user asks to check whether a design run achieved what was asked.
---

# Check whether a design run achieved what was asked

Decide whether a design run actually satisfied its intent (FORGE-539).

Call `flow.lifecycle` with the run id (or `flow.verify_completion` where it is served). Its `completion.classification` is the verdict, and only `COMPLETED_VERIFIED` means the intent is satisfied: every mandatory requirement passes with current evidence, no result is stale, every phase's objective was met and no blocking gap remains.

`PARTIALLY_COMPLETED` means the phases finished and the intent did NOT. Never report that as done: list `unmet_requirements`, the phases whose `validity` is STALE or POTENTIALLY_INVALID, and the blocking gaps, and say what would close each one. `COMPLETED_WITH_WARNINGS` passed, with the warnings as real limits of the result. Read `limits`: anything listed there was not checked. Then do what `next_step` says.
