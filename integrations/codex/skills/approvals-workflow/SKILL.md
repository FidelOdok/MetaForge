---
name: approvals-workflow
description: See what is waiting for your decision, and decide it here in MetaForge. Use when the user asks to see what is waiting for your decision, and decide it here.
---

# See what is waiting for your decision, and decide it here

List what is waiting for a person's decision: design-flow gates, held tool calls, change proposals and twin approvals.

Call `approvals.list` (pass the active `project_id` to narrow it). Show each item: its kind, title, project, what its checks found and the decisions it allows. Nothing pending is a real answer; say so.

To have the person decide one, call `approvals.decide` with its id. THEY answer in a prompt this client shows them; you do not answer it, suggest an answer as if it were theirs, or tell them it was decided when it was not. Reject, retry and rework need their reason. If they dismiss the prompt, or `approvals.decide` is not in your tool list, the item stays pending for them in the dashboard or `forge approvals`.
