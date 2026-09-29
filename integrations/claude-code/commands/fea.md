---
description: Run a load case and record the evidence
---

Run structural analysis on a committed part.

Stage the geometry with `twin.stage_work_product_file`, set up the load case, run `calculix.run_fea`, then check convergence with `calculix.check_mesh_convergence` and cross-check against a hand calculation where one applies.

Record the result with `twin.record_evidence`, pinned to the exact revision it came from. A number with no evidence behind it is not a result — say what you could not establish rather than rounding it into a claim.
