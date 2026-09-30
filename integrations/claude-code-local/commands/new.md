---
description: Start a project, or open the one the user means
---

Create or open a MetaForge project from what the user said.

If they named an existing project ('open the arm project'), call `project.open` with their words as `query`. It resolves an id, an exact name, or a unique name substring. Several matches comes back as an error listing them — ask which one, do not pick.

If they described something new ('new project: 6-DOF arm, 1 kg payload'), call `project.open` first anyway to check it does not already exist; a second project with the same subject splits the design thread in two and nothing reconciles them. Only on `no project matches` call `project.create`, with a short `name` and the user's own words as `description`.

Both tools scope the session to the project. Check `scope_bound`: false means you must pass `project_id` explicitly on every later call.

Then record what they already told you. Figures in the intent — '1 kg payload', '6-DOF', a reach, a duty cycle — are requirements, and belong in the twin as constraints rather than only in the chat, or the first gate has nothing to check against. Do not invent values they did not give, and do not round the ones they did.
