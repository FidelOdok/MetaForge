---
name: imagine_studio
description: Start or update the Imagine studio for a product from what the person said, with the twin tools, and after the person hands it over start Develop. Use when a project has no studio yet and the person describes a product ("a desk lamp with a USB-C cable and a touch button"), when they ask to start, fix or fill the Imagine studio, or when the studio is handed over and they ask to proceed to Develop. Not for editing a single part's CAD (use design_cad_iterative) or for a gated multi-phase design flow (use intent-to-verified-design).
---

# Imagine studio from a prompt

The Imagine studio is the formgraph product page hosted by MetaForge. It holds
one product config (`IMG-STUDIO`): what the product is for, its requirements,
real parts, the process and quantity, where it lives, and the programme
records (intent ledger, assumptions, markets). The person shapes it in the
browser; the handover to engineering is theirs.

You write the config with three tools:

| Tool | Does |
|---|---|
| `twin.imagine_get_studio` | Reads the studio: status (none, draft, handed_over), revision, config, and what the person did in the browser. Reads only. |
| `twin.imagine_put_studio` | Writes the config as the next revision. The formgraph checks run first; any error refuses the write and lists every problem. |
| `twin.imagine_handover_status` | The handover checklist: each item done, missing or assumed. Reads only. |

All three take `project_id` (defaults to the chat's project). Two more are a
person's steps, open to you only in a delegated run (rule 2):

| Tool | Does |
|---|---|
| `twin.imagine_accept` | Accepts assumptions and confirms scenario tests for the person you act for. Recorded as yours on their behalf, never as theirs. |
| `twin.imagine_handover` | Hands the studio over to Develop once the checklist is ready. |

## Rules

1. **Nothing above V2.** The studio is captured (V0), consistent (V1) and
   screened (V2) at most. Never call a studio result analysed, verified or
   qualified. That needs real solvers and checks against exact CAD in Develop.
2. **Hand over only when delegated.** The handover is the person's decision.
   Only when the gateway owner lets agents decide a person's steps
   (`METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY=true`, see docs/approvals.md) and
   your connection names you and the person (`X-MetaForge-Agent`,
   `X-MetaForge-On-Behalf-Of`) is the run **delegated**. Then you may call
   `twin.imagine_accept` and `twin.imagine_handover` (the latter only once
   `twin.imagine_handover_status` says ready); each is recorded as your
   decision on the person's behalf, never as theirs. Otherwise, or when a
   tool is refused, held or missing, never accept or hand over: tell the
   person what is missing and that the "Hand over to engineering" button is
   theirs. `twin.imagine_rework` follows the same rule.
3. **No invented part numbers.** A part's order code, maker and datasheet come
   from a source you read, with the date you read it. When you have none, keep
   the placeholder and list the part as missing; never make one up.
4. **Every number has a source.** A value the person did not give goes in
   `programme.assumptions` and its requirement's `source` names that assumption.
5. **Read before you write.** If `twin.imagine_get_studio` shows a draft, start
   from its config so the person's choices carry over. A handed-over studio is
   read only; a person reopens it.

## The config, right first time

These are the checks a live run tripped over. Get them right before the first
write:

- **Top-level keys are fixed:** `engine`, `product`, `form`, `process`, `power`,
  `functions`, `requirements` (all required), plus `scenarios`, `simulations`,
  `decisions`, `environment`, `programme`, `context`, `catalogue`, `view`,
  `level`, `color`, `ambient`, `nominations`, `airframe`, `publish`, `$schema`.
  Anything else is refused. In particular:
  - `intent_ledger` goes in `programme.ledger`;
  - `assumptions` goes in `programme.assumptions`;
  - `use_scenarios` goes in `scenarios` (top level, each with `requires`);
  - `parts` goes in `context.components.parts`;
  - `options` goes in `context.options.decisions`.
- **`product` is required**, with `id` a lowercase slug (letters, digits and
  hyphens, 2 to 49 characters, for example `desk-lamp`) and `name` of 60
  characters or fewer.
- **`engine`** is `formgraph@2` (`formgraph@1` also passes).
- **`form.template`** must be one the catalogue has. Today that is only
  `enclosure` (a soft-shell box with a lid). Use it as the shell or base and
  say in the ledger what the template cannot show.
- **`requirements[].pri`** is `Must`, `Should` or `Off`, exactly. Ids come from
  the catalogue: `interval`, `life`, `resp`, `cov`, `acc`, `drop`, `touch`,
  `chgt`, `chgh`, `ip`. Anything else is a custom requirement in
  `programme.customRequirements` (pri `Must` or `Should`).
- **Catalogue ids:** functions `e_th`, `e_imu`, `e_gnss`, `e_lte`, `e_lora`,
  `e_btn`, `e_led` (a status light), `e_ldrv` (a constant-current LED driver
  for a lamp's main light), `e_buzz`, `e_usbc`, `e_ext` (one radio at most);
  power board `none`, `mcu`, `rpiz2w`, `rpi4`, `custom`; battery `none`,
  `c18650`, `p103450`; process `im` (moulding), `cnc`, `sls` or `fdm`
  (desktop FDM on a Bambu Lab P1S-class printer: materials `petg`, `pla`,
  `petgcf`, `asa`; finishes `asp`, `fine`, `sanded`; fixings `ins25`,
  `ins30`, `pt25`, `pt30`), each with its own materials, finishes and
  fixings (the error lists the allowed ones). A library material named for
  its family, such as `prusament_petg`, is accepted for `fdm`: it screens as
  `petg` and the twin keeps your name. Never stand one process in for
  another: FDM screens with FDM rules.
- **The manufacturing context** is `process`: `id`, `material` and `qty` (a
  whole number of units) are what the handover needs. The quantity comes from
  the person; if they have not said, record it as an assumption.
- **Parts** in `context.components` need `schemaVersion: 1`, and each part
  `id`, `role` (`fn:<function>`, `board` or `battery`), `mpn`, `manufacturer`,
  `datasheet`, `retrieved`, `interfaces` and `geometry.envelope` with a
  `source`.

This skeleton passes the checks as written. Replace the values with the
product's own, keep the shape:

```json
{
  "engine": "formgraph@2",
  "product": {"id": "desk-lamp", "name": "Desk Lamp", "brief": "<the person's own words>"},
  "form": {"template": "enclosure", "params": {"W": 120, "H": 40, "t": 2.2}, "features": ["bosses", "ribs"]},
  "process": {"id": "fdm", "material": "petg", "finish": "asp", "fixings": "ins25", "qty": 20},
  "power": {"board": "mcu", "battery": "none"},
  "functions": ["e_usbc", "e_btn", "e_ldrv"],
  "requirements": [
    {"id": "resp", "pri": "Must", "values": {"resp": 0.5}, "source": "person, chat", "verify": "test"},
    {"id": "drop", "pri": "Should", "values": {"drop": 0.8}, "source": "assumption a-drop", "verify": "test"},
    {"id": "ip", "pri": "Off", "values": {"ip": "none"}, "source": "indoor use, person"}
  ],
  "scenarios": [
    {"id": "switch-on", "text": "Someone taps the button and the light comes on", "requires": ["resp"]},
    {"id": "knocked", "text": "It is knocked off the desk onto the floor", "requires": ["drop"]}
  ],
  "simulations": ["drop", "thermal"],
  "environment": {"opMin": 5, "opMax": 35, "storeMin": -10, "storeMax": 50, "humidity": [20, 80], "condensing": false, "sun": "none", "altitude_m": 500, "vibration": "fixed", "outdoor": false},
  "programme": {
    "phase": "define",
    "markets": ["UK"],
    "segment": "consumer",
    "sector": "luminaire",
    "ledger": [
      {"text": "A desk lamp", "maps": ["fn:e_ldrv"], "status": "mapped"},
      {"text": "turned on with a button", "maps": ["fn:e_btn", "req:resp"], "status": "mapped"},
      {"text": "powered over USB-C", "maps": ["fn:e_usbc"], "status": "mapped"},
      {"text": "looks good on a desk", "maps": [], "status": "out-of-scope", "note": "set in the studio's look and finish"}
    ],
    "assumptions": [
      {"id": "a-drop", "text": "A fall from a desk is about 0.8 m", "ref": "requirement drop"},
      {"id": "a-qty", "text": "A first batch of 20 units", "ref": "process qty"}
    ],
    "rationale": {"process": "Desktop FDM suits a 20 unit batch with no tooling", "material": "PETG is tough and takes a lamp's warmth", "board": "small MCU board drives the light and reads the button", "battery": "mains powered over USB-C, no cell", "size": "smallest shell that fits the parts"},
    "interfaces": [
      {"kind": "electrical", "a": "usbc-a", "b": "board", "what": "5 V supply"},
      {"kind": "mechanical", "a": "btn-a", "b": "shell", "what": "button through the top face"}
    ],
    "firmware": {"uniqueCredentials": true, "vulnPolicy": "published on the support site", "supportYears": 3}
  },
  "context": {
    "components": {"schemaVersion": 1, "parts": [
      {"id": "usbc-a", "role": "fn:e_usbc", "name": "USB-C receptacle", "mpn": "<order code read from the datasheet>", "manufacturer": "<maker>", "datasheet": "https://<maker>/<datasheet>.pdf", "retrieved": "2026-11-07", "mass": 1.0,
       "interfaces": [{"id": "vbus", "bus": "usb", "dir": "bi"}],
       "geometry": {"envelope": [9.0, 7.5, 3.2], "source": "datasheet mechanical drawing", "body": [{"shape": "box", "at": [0, 0, 1.6], "size": [9.0, 7.5, 3.2]}]}},
      {"id": "btn-a", "role": "fn:e_btn", "name": "Tactile switch", "mpn": "<order code>", "manufacturer": "<maker>", "datasheet": "https://<maker>/<datasheet>.pdf", "retrieved": "2026-11-07", "mass": 0.5,
       "interfaces": [{"id": "sw", "bus": "gpio", "dir": "out"}],
       "geometry": {"envelope": [6.0, 6.0, 4.3], "source": "datasheet mechanical drawing", "body": [{"shape": "box", "at": [0, 0, 2.15], "size": [6.0, 6.0, 4.3]}]}},
      {"id": "ldrv-a", "role": "fn:e_ldrv", "name": "Constant-current LED driver (buck)", "mpn": "<order code>", "manufacturer": "<maker>", "datasheet": "https://<maker>/<datasheet>.pdf", "retrieved": "2026-11-07", "mass": 0.3,
       "interfaces": [{"id": "pwm", "bus": "pwm", "dir": "in"}, {"id": "vin", "bus": "pwr", "dir": "in", "v": [4.5, 5.5]}],
       "geometry": {"envelope": [3.0, 3.0, 1.0], "source": "datasheet mechanical drawing", "body": [{"shape": "box", "at": [0, 0, 0.5], "size": [3.0, 3.0, 1.0]}]}}
    ]},
    "options": {"schemaVersion": 1, "decisions": [
      {"id": "btn", "kind": "part", "label": "Button", "candidates": ["btn-a"], "pick": "btn-a", "why": "<reason>"}
    ]}
  }
}
```

The angle-bracket values pass the checks but are not facts. Replace each with
what you read, or list it to the person as missing.

## Steps

1. **Read the studio** with `twin.imagine_get_studio`. Handed over: go to
   "After handover". A draft: start from its config.
2. **Work from what the person said.** Split their words into ledger
   statements; map each to a function, a requirement or a scenario, mark it
   `custom` with a custom requirement, or `out-of-scope` with a note. Ask at
   most three questions, in one message, and only for what you cannot infer
   (quantity, where it is used, how it is powered are the usual ones). Put
   every value you assumed in `programme.assumptions`.
3. **Write it** with `twin.imagine_put_studio` and the full config, including
   `programme` (ledger, assumptions, markets), `environment`, `scenarios`,
   `programme.interfaces` and `process.qty`.
4. **Fix every error in one retry.** A refusal lists all the problems at once.
   Fix all of them and write again. If the second write is refused, stop and
   tell the person which checks still fail; do not loop.
5. **Check the handover** with `twin.imagine_handover_status`.
6. **Tell the person, briefly:** the studio is ready to open; what to do there
   (confirm or accept each assumption, confirm the scenario tests, review the
   ledger, pick between part options, generate a concept mesh, let the
   screening run); which checklist items are missing; that results are
   screening only (V2 at most); and that the handover button is theirs.
7. **Delegated runs only:** when the run is delegated (rule 2), close the
   checklist yourself instead of stopping, in this order:
   1. **Accept.** `twin.imagine_accept` with the open assumption ids and the
      inferred scenario ids from `twin.imagine_handover_status`. The checklist
      then reads them as accepted by you on the person's behalf.
   2. **Concept mesh.** `twin.render_concept_image` from the studio's layout,
      then `twin.generate_concept_mesh` with that image's `node_id` (it
      returns a job; poll `twin.job_status`). Both run under the project's
      approval policy and spend cap. The mesh lands in the session's project,
      which is what the checklist counts.
   3. **Hand over.** When `twin.imagine_handover_status` says ready,
      `twin.imagine_handover` with a fresh `op_id`.
   4. **Record each delegated decision** with `twin.record_decision`: what you
      accepted or handed over, for whom, and why.

   If any step is refused or held, stop there and say what waits for the
   person. Otherwise ask the person to accept and hand over.

## After handover

When the studio reads `handed_over` and the person asks to proceed:

1. **Read what was handed over.** `twin.imagine_get_studio` (the frozen config
   and the person's choices), `twin.get_manufacturing_context` (process,
   material, quantity, with authority), and the records the handover created:
   `twin.item_history` with `CS-IMAGINE` (the requirements),
   `IFC-CAD-EDA` and `IFC-EDA-FIRMWARE` (the proposed interface contracts),
   and `twin.find_by_property` or `twin.get_node` for the concept assembly
   decision and its `CMP-` component ids.
2. **Start Develop, in this order:**
   1. **Concept mesh.** If none exists, `twin.render_concept_image` then
      `twin.generate_concept_mesh` (it returns a job; poll `twin.job_status`).
      It is illustrative, never a dimensioned model. `twin.segment_concept_mesh`
      splits it into parts when the person wants that.
   2. **Mechanical parts.** For each made part, `twin.design_cad_loop` with the
      manufacturing context and the `IFC-CAD-EDA` envelope; read the result
      with `twin.get_dfm_report` and say which DFM findings remain.
      In a client that writes code itself (Claude Code, Codex), write each
      part's CadQuery yourself with the generate_cad_script skill instead
      (`cadquery.execute_script`, then `twin.commit_geometry` and
      `twin.render_work_product` to see it): `twin.design_cad_loop` runs its
      models on the gateway and spends the gateway's model budget.
   3. **Electronics and firmware** against the contracts: the buses and parts
      in `IFC-EDA-FIRMWARE` and `IFC-CAD-EDA`, for example
      `kicad.generate_schematic` for the board and `twin.create_firmware_scaffold`
      for the firmware. A change that breaks a contract is a proposal to the
      person, not an edit.
   4. **Check.** `twin.evaluate_dfm` and `twin.get_dfm_report` on each part,
      `twin.constraint_violations`, and the analyses the requirements call for
      (`calculix.run_fea`, `calculix.run_thermal`).
   5. **Make.** `twin.get_release_checklist`, then the release package with
      `twin.create_release_package` when the checklist allows it.
3. **Approvals belong to the person.** Every proposal, contract change, gate
   and release waits for them. Say what is waiting and stop; never work round
   an approval or report one as decided.
4. **A delegated run carries on.** When the run is delegated (rule 2), do not
   stop between stages to ask: go from the handover through Develop, Check
   and Make in the order above. Anything the project's policy approves runs;
   anything held for a person stops the run, and you say what is waiting.
   Record each decision you take on the person's behalf with
   `twin.record_decision`, naming them, and never report one as theirs.
