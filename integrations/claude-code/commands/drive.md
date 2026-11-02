---
description: Drive a design run until it is verified, restarting or fixing as it goes
---

Put a design run under the run supervisor and report how it is going.

Take the run id the person gave; if none, list the project's runs with `flow.status` and ask which, or offer `/metaforge:flow` to start one.

Call `flow.supervise` with `action: start` (budgets are optional: `max_restarts`, `max_model_calls`, `max_hours`). The supervisor watches the run, gets each gate reviewed and decided within the project's approval policy, restarts a failed or unverified run with an amended brief, and stops for a person when only a person can act. It never decides a gate itself, and neither do you.

Then report `flow.supervise` with `action: status`: its status, the chain of runs, the budgets used and the last log entries. When it says a gate waits for a person, offer `/metaforge:approvals`. When it stopped on a platform issue, say plainly that it needs a fix, not another attempt.

To keep reporting, the person can run `/loop 10m /metaforge:drive <run id>`.
