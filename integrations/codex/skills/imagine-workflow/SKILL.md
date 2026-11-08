---
name: imagine-workflow
description: Start the Imagine studio for this product, then develop it after handover in MetaForge. Use when the user asks to start the Imagine studio for this product, then develop it after handover.
---

# Start the Imagine studio for this product, then develop it after handover

Start or update the active project's Imagine studio, following the `imagine_studio` skill.

Call `session.start` with the project first. If your client does not keep the MCP session (a headless run, or a 2026-07-28 client), pass the `session_handle` it returns as the `metaforge_session` argument on every later call, so each call stays in the project and its approval policy applies, including to tools with no `project_id` argument.

Then call `twin.imagine_get_studio`. With no studio, or a draft, build the formgraph config from what the person said, using the skill's skeleton and rules, asking at most three questions. Write it with `twin.imagine_put_studio`. A refusal lists every failed check: fix all of them and write once more. If that is refused too, stop and say which checks still fail.

Then call `twin.imagine_handover_status` and report each item that is missing or assumed, and what the person does in the studio to close it (confirm assumptions and scenarios, review the ledger, pick parts, generate a concept mesh). Results are screening only, V2 at most. Never call the studio ready when the checklist says it is not. The handover is the person's decision. If the gateway lets agents decide a person's steps (the owner set `METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY`) and your connection names you and the person you act for (`X-MetaForge-Agent`, `X-MetaForge-On-Behalf-Of`), the run is delegated: do not stop. Accept the open assumptions and confirm the inferred scenarios with `twin.imagine_accept`; make the concept mesh with `twin.render_concept_image` then `twin.generate_concept_mesh` (poll `twin.job_status`); hand over with `twin.imagine_handover` once the checklist is ready; and record each of these with `twin.record_decision` as yours on the person's behalf. Otherwise, or if a tool is refused, held or missing, ask the person to accept in the studio and press Hand over to engineering in the dashboard.

When the studio is already handed over and the person asks to go on, follow the skill's After handover section: read the handed-over records, then develop in order (concept mesh, mechanical parts, electronics and firmware against the interface contracts). For each mechanical part, write the CadQuery yourself with the `generate_cad_script` skill (`cadquery.execute_script`, then `twin.commit_geometry`, and `twin.render_work_product` to see it) rather than `twin.design_cad_loop`, which runs its models on the gateway and spends the gateway's model budget. Then Check (DFM, constraints, the analyses the requirements call for) and Make (the release checklist and package). In a delegated run carry on through Develop, Check and Make without stopping to ask, recording each decision you take for the person. Writes may be held for approval; an approval that waits for the person stops the run, so say what is waiting and stop.
