---
description: Run a load case and record the evidence
---

Run structural analysis on a committed part.

For a committed CAD item, `twin.run_item_fea` runs the whole chain in one call: name the part by `item_key`, give a `load_case` (material, the `fixed` and `loaded` surfaces by name or `{axis, side}`, and `force_n`), and it reads the STEP, meshes it with `freecad.generate_mesh` and solves with `calculix.run_fea`. To drive the steps yourself: stage the geometry with `twin.stage_work_product_file`, mesh it with `freecad.generate_mesh` (CalculiX needs that mesh), pick the surfaces from its `faces` table, and run `calculix.run_fea`. Then check convergence with `calculix.check_mesh_convergence` and cross-check against a hand calculation where one applies.

Record the result with `twin.record_document` (`document_type: simulation_result`), pinned to the exact revision it came from with `analysed_geometry_node_id` and `analysed_geometry_revision`, and pass the solver's `field.file` as `field_file`. If `twin.record_evidence` is in your tool list, also record the evidence against the requirement; if it is not, say the requirement link was not recorded (FORGE-533). A number with no evidence behind it is not a result — say what you could not establish rather than rounding it into a claim.
