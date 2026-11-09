---
name: formgraph
description: Generate, edit, review and release Formgraph product environments. Use when someone describes a physical product (an enclosure, device, sensor, tracker, hub, handheld) and wants an interactive page to capture its intent and requirements, shape it parametrically under design-for-manufacturing limits, add electronics and wiring, simulate it, run the programme from Define through EVT, DVT and PVT to production, and produce a verifiable BOM. Also use to review a pasted Formgraph validation pack, run a phase gate review, or release a product revision.
---

# Formgraph

Formgraph turns a product brief into a self-contained web page. It has two views:

- **Design** (clean layout: each section collapses to a one-line summary with a status dot):
  - requirements, assumptions and your own requirements
  - where it lives: environmental checks at the hot and cold extremes
  - a parametric enclosure held inside DFM limits
  - electronics with typed wiring, a playable virtual device and product tests
  - background validation, readiness gates and a fingerprinted BOM
- **Programme:** everything from the hardware product design guide.
  - phases with exit criteria and loops, the effort matrix, the intent ledger
  - V-model traceability, architecture and interfaces, budgets, trade-offs, FMEA
  - test plan, compliance, schedule, electronics depth, firmware plan, simulation verification
  - manufacturing and supply, industrial design and human factors, lifecycle, business
  - all 78 domains with owners and evidence, and the primitives map

The engine never changes per product. For each product you build its **environment**:
- a **product config** (JSON) made of Formgraph primitives and programme records
- a **context** folder: real parts with interfaces and geometry, the machines that can make it, every option, generated models and the user's scenario tests

The page builds the 3D design, the wiring, DFM and DFA, and the tier 1 and 2 simulations from that context, and lets the user choose between options and edit the context. The config plus context is the starting point for cloud design. Never edit `runtime/engine.html` for a single product.

Paths below are relative to this skill's base directory.

| File | Use |
|---|---|
| `data/catalogue.json` | Ids for templates, parameters, processes, materials, finishes, fixings, boards, batteries, functions, requirements, simulations, decisions |
| `data/programme.json` | The guide as data: phases, loops, effort matrix, V-model, 78 domains, disciplines, tests, standards, factors |
| `data/product.schema.json` | Shape of a product config, including `programme` |
| `data/suppliers.uk.json` | Built-in real UK suppliers by process, with location, machines, materials, limits, build size, lead time, price basis and source |
| `data/machines.default.json` | Typical machine capabilities, merged under the user's registry and the UK suppliers |
| `scripts/fg_context.py` | Validates and compiles the context folder (used by `build_page.py`) |
| `references/context.md` | Real parts, interfaces, geometry, machines and options |
| `references/models.md` | Model formulas for tier 1 and tier 2, and binding them to requirements |
| `references/scenarios.md` | Asking for scenario tests and writing them |
| `references/references.md` | Reference images, sketches, video frames and meshes, and the checks against them |
| `scripts/ref_tools.py` | Capped video frames, image shrinking and mesh statistics |
| `references/view.md` | Parts-first readiness and the product's own view: key questions, summary, section and studio order, Try it mode |
| `references/airframe.md` | Multirotor airframe in 3D, design variants, airframe checks and CAD export |
| `scripts/export_cad.py` | Writes the page's CAD (GLB and STL) into the product folder |
| `scripts/build_page.py` | Validates a config and builds the page. Standard library only |
| `references/primitives.md` | The design primitives and the rules that join them |
| `references/intent-mapping.md` | Brief to primitives, the intent ledger, assumptions, custom requirements and programme fields |
| `references/programme.md` | Phases, exit criteria, what the page computes and what it tracks |
| `runtime/engine.html` | The engine. Do not edit |
| `../../examples/*.json` | Three complete configs to copy from |

## Workflow: new product

1. **Capture intent.** Read `references/intent-mapping.md`.
   - Split the brief into statements and record every one in `programme.ledger`. Each statement is mapped to primitives, captured as a custom requirement, or marked `out-of-scope` with a note. Nothing is dropped silently.
   - Ask only what you cannot infer, at most three questions in one message.
2. **Read references** if the user gave images, sketches, video or meshes (read `references/references.md`). Ask what each is for (look, target, envelope, context) when it is not clear. Pull a capped set of frames from video; never read a whole clip.
3. **Ask for scenario tests.** Always. Read `references/scenarios.md`. In one message, ask how it is used, misused and what fails around it, and propose a starter set to accept or change. Do not continue until the user has answered or told you to use the inferred set.
4. **Record assumptions.** Every number or choice you assumed rather than read from the brief goes in `programme.assumptions`. The page will not close the Define phase until the user confirms each one.
5. **Gather context (compulsory).** Read `references/context.md`. Create `formgraph-products/<id>/context/`.
   - **Parts:** for every function, the board and the battery, search the web for at least two real candidates. Record order code, maker, datasheet link, the date you read it, cost at the quantity, mass, interfaces and geometry from the mechanical drawing. Only skip a real part when the user excludes it, and then add a flagged stand-in.
   - **Machines:** the built-in UK suppliers (`data/suppliers.uk.json`) are always available. Read `~/.formgraph/machines.json` if it exists. Search for more suppliers only where a process has fewer than two, or for markets outside the UK, and record their published design rules.
   - **3D before CAD (compulsory):** every part you pick must have `geometry.envelope` from its drawing, with a source, plus a simple `body`. The builder refuses a page while a picked part has none, and the page blocks CAD export. Research the drawing, or pick a candidate that has one.
   - **Options:** one decision for each function, the board, the battery, the machine, the material and the fixing, with candidates, the default pick and why.
6. **Write scenarios and models.**
   - Scenarios in the user's words, each with the requirement ids that prove it, in `product.json` and as tests in `context/scenarios.json`.
   - Models for whatever the built-in checks do not cover (read `references/models.md`), each with a source. Bind custom requirements to model outputs with `model`, `op` and `limit`.
7. **Choose primitives.**
   - **Requirements:** values, priority and a `source` for every number.
   - **Custom requirements** (EARS, with measure, verification method, V-model level and phase) for intent the built-in requirements cannot express.
   - **Design:** functions, power, process (with `machine`), form params and features, simulations. Form params are a starting point; the page sizes the form around the packed parts.
   - **View (compulsory):** write the product's own `view` (read `references/view.md`): the key questions it must answer yes to, its headline numbers, section and studio order, what to hide, and a Try it mode that fits it. Nothing on the page stays generic.
   - **Airframe:** for a multirotor, add the `airframe` block and the battery and prop variants (read `references/airframe.md`), so the page shows the whole aircraft and checks its layout.
8. **Fill the environment** block (operating and storage range, humidity and condensing, sun, altitude, salt, UV hours, vibration profile, vent membrane) from `references/intent-mapping.md`, and record each value you assumed in `programme.assumptions`.
9. **Fill the programme:**
   - `markets`, `segment`, `sector`, `environment`, `start`
   - `rationale` for process, material, board, battery, radio and size
   - `firmware` security fields, `budgets` targets, `business` price and margin, `lifecycle`, `flows`
   - any extra `tradeoffs`, `risks` and `interfaces`

   Leave out what the brief does not support and say so.
10. **Write the config** to `formgraph-products/<id>/product.json` in the user's current working directory, with `"context": "context"`. Start from the closest example.
11. **Build:**
    ```bash
    python "<skill dir>/scripts/build_page.py" formgraph-products/<id>/product.json
    ```
    Fix every `ERROR`. Act on or explain every `WARN`.
12. **Publish.** If the Artifact tool is available, publish the HTML (icon `cube`, a one-sentence description) with `capabilities: {"db": {}, "user": {}, "downloads": true}` so choices and context edits come back and CAD can be saved, and record the link in `product.json` as `"publish": {"url": "..."}`. Otherwise give the path. Say the page is private until shared.
13. **Hand over.** Cover, briefly:
    - the assumptions to confirm and the scenario tests to confirm
    - what was read from each reference and how the design compares
    - parts researched, with how fresh the prices are, and any stand-ins
    - statements that became custom requirements or were ruled out
    - requirements expected at risk, and requirements with no model
    - what to do first: confirm assumptions, compare options, run scenarios, Try it, Programme view
    - that `/formgraph:sync <id>` brings their choices and edits back
    - the CAD: run `scripts/export_cad.py` on the page and list the files in `cad/`

## Workflow: edit a product

Change `formgraph-products/<id>/product.json`, rebuild, republish to the same artifact. Keep `product.id` unchanged (it is the page's storage key). Record why: a `source` for requirement values, `rationale` for design choices, a `tradeoffs` entry for a decision between options.

## Workflow: review

Input is the text from **Copy validation pack**. It ends with a `STATE JSON` line holding:
- gates, requirements, DFM and electronics issues, BOM and jobs
- `programme`: phase, exit criteria, domains, budgets, risks, tests, standards, interfaces, reliability, tolerance, carbon, repair score, security, schedule

Report in this order:
1. Phase and what blocks its exit.
2. Gates.
3. Must requirements not met.
4. Failures, each with a concrete fix.
5. Top risks without mitigation.
6. Untested requirements.
7. Domains failing or at risk.
8. BOM lines without part numbers.

Keep screening results (Rule, Provisional, Estimated) apart from validated ones. For long packs, use the `formgraph-reviewer` agent.

## Workflow: phase gate review

Use the pack's `programme.exit` list for the current phase. For each criterion that is not passing, say what evidence or change closes it and who owns it (from the domains list). Recommend move on, hold, or send back, quoting the guide's loops: EVT to Concept, DVT to EVT, PVT to DVT. Moving phase happens on the page; you only recommend.

## Workflow: release

1. Require gates 1 to 3 passing and every BOM line `verified`. Otherwise list what blocks release and stop.
2. Write `formgraph-products/<id>/releases/<rev>.md` with:
   - fingerprint, phase, gates, requirements with status and confidence
   - the BOM, the test plan with status
   - standards, the ECO list since the previous release
   - cloud solver runs and physical tests still due

   Follow it with the full pack text.
3. Only if the user asks and the Atlassian tools are connected, add the record to the named Jira issue after confirming the key.

## Rules

- Use catalogue ids, real parts from the context, or add ids through the config's `catalogue` block with typical values clearly marked.
- Every requirement value needs a `source`; every design choice needs a `rationale`; every assumption goes in `assumptions`.
- Catalogue values and programme factors (failure rates, carbon factors, lead times, anthropometrics) are typical.
- Part numbers come only from a source you read (datasheet or maker page, linked, with the date). Never invent one. Test results and certificates come from people, never from you.
- Everything before gate 4 is early screening; tracked domains stay open until someone attaches evidence. Repeat this when summarising.
- Brand pages as MetaForge (metaforge.uk). Do not use em dashes in anything you write.
