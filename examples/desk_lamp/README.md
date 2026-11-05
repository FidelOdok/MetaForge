# Desk lamp golden path

The first multidisciplinary demonstration: a low voltage, USB-C powered,
dimmable desktop lamp across CAD, EDA, firmware, simulation and
manufacturing. This folder holds **step 1**, the product definition and the
cross-domain interface contracts.

```
examples/desk_lamp/
├── definition.json   # The lamp as data: brief, references, manufacturing context,
│                     # concept assembly, interface contracts, golden-path steps
├── PRD.md            # The written brief (the specification reference)
└── README.md         # This file
```

## Run it

With a gateway running:

```bash
python scripts/demo_lamp.py --gateway http://localhost:8000 --project-dir desk-lamp
```

The runner seeds the project (`POST /v1/examples/desk-lamp/seed`), checks the
four interface contracts, prints their status and the steps still missing,
and writes `desk-lamp/.forge/demo_manifest.json`. Seeding is idempotent and
never overwrites anything a person changed.

At step 1 every contract is `proposed` and every check is `not_evaluated`:
there is no CAD, PCB, firmware or analysis yet, and the demo says so instead
of inventing results. Later steps fill them in.

The full walkthrough is in the docs: `docs/examples/desk-lamp.md`.

## Editing the definition

`definition.json` is read by the gateway at seed time (the image copies it to
`/app/examples`; `METAFORGE_EXAMPLES_DIR` overrides the folder). Every value
says how far it can be trusted: a contract parameter is `stated`,
`estimated`, `assumed` or `unknown`, and a manufacturing context field is
`user_stated` or an `assumption`. Keep it that way: an estimate is never a
statement, and an unknown is never filled with a guess.
