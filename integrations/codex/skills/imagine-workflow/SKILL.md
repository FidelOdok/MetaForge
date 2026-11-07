---
name: imagine-workflow
description: Start the Imagine studio for this product, then develop it after handover in MetaForge. Use when the user asks to start the Imagine studio for this product, then develop it after handover.
---

# Start the Imagine studio for this product, then develop it after handover

Start or update the active project's Imagine studio, following the `imagine_studio` skill.

Call `twin.imagine_get_studio` first. With no studio, or a draft, build the formgraph config from what the person said, using the skill's skeleton and rules, asking at most three questions. Write it with `twin.imagine_put_studio`. A refusal lists every failed check: fix all of them and write once more. If that is refused too, stop and say which checks still fail.

Then call `twin.imagine_handover_status` and report each item that is missing or assumed, and what the person does in the studio to close it (confirm assumptions and scenarios, review the ledger, pick parts, generate a concept mesh). Results are screening only, V2 at most. The handover is the person's button in the dashboard: never hand over for them, and never call the studio ready when the checklist says it is not.

When the studio is already handed over and the person asks to go on, follow the skill's After handover section: read the handed-over records, then develop in order (concept mesh, mechanical parts, electronics and firmware against the interface contracts). For each mechanical part, write the CadQuery yourself with the `generate_cad_script` skill (`cadquery.execute_script`, then `twin.commit_geometry`, and `twin.render_work_product` to see it) rather than `twin.design_cad_loop`, which runs its models on the gateway and spends the gateway's model budget. Writes may be held for approval; approvals belong to the person, so say what is waiting and stop.
