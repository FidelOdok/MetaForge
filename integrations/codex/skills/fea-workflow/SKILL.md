---
name: fea-workflow
description: Run a load case and record the evidence in MetaForge. Use when the user asks to run a load case and record the evidence.
---

# Run a load case and record the evidence

Run structural analysis on a committed part.

Stage the geometry with `twin.stage_work_product_file`, set up the load case, run `calculix.run_fea`, then check convergence with `calculix.check_mesh_convergence` and cross-check against a hand calculation where one applies.

Record the result with `twin.record_document` (`document_type: simulation_result`), pinned to the exact revision it came from with `analysed_geometry_node_id` and `analysed_geometry_revision`, and pass the solver's `field.file` as `field_file`. If `twin.record_evidence` is in your tool list, also record the evidence against the requirement; if it is not, say the requirement link was not recorded (FORGE-533). A number with no evidence behind it is not a result — say what you could not establish rather than rounding it into a claim.
