# api_gateway/imagine

The backend of Create's Imagine stage (FORGE-821): the formgraph concept
studio as a twin item, its browser bridge and the handover gate to Develop.
The behaviour is documented in [`docs/imagine-stage.md`](../../docs/imagine-stage.md).

## Provenance

The studio is **formgraph 2.7.0**, a Claude Code plugin merged into MetaForge
on 7 November 2026 at the owner's decision. The standalone plugin keeps its
own copy; this copy is restyled to MetaForge and hosted by the gateway.

| Here | Plugin source | Changes |
|---|---|---|
| `engine/engine.html`, `engine.css`, `engine.js` | `skills/formgraph/runtime/engine.html` | Split into page, stylesheet and script so the page runs under a CSP with no inline script; MetaForge colour tokens (light and dark) and font stack, no Google Fonts; three.js and the GLTF exporter from the gateway instead of cdnjs and jsdelivr; the MetaForge host bridge (`fg:*` messages) replaces `claude.use('db')` when hosted, and the standalone path is kept; read-only mode after the handover; `?theme=` URL parameter; no inline event handler; BOM lines carry their component id; the footer, the validation pack and the export metadata are branded MetaForge |
| `formgraph/build_page.py` | `skills/formgraph/scripts/build_page.py` | Package-relative imports, data folder beside it, no command-line entry point |
| `formgraph/fg_context.py` | `skills/formgraph/scripts/fg_context.py` | Package-relative imports, data folder beside it, the per-user machine registry is not read, no context folder defaults to the working directory |
| `formgraph/fg_refs.py` | `skills/formgraph/scripts/fg_refs.py` | A hosted context (no folder) never opens a `file`; packed references are accepted |
| `formgraph/data/*.json` | `skills/formgraph/data/` | Branding only: the product schema's `$id` and the `brand` default are MetaForge's (`https://www.metaforge.uk/schemas/formgraph/product-1.json`). Otherwise unchanged |
| `formgraph/SKILL.md`, `formgraph/references/` | `skills/formgraph/` | Branding only: pages are branded MetaForge. Otherwise unchanged; the agent's guide to writing a product config |

Not merged: `ref_tools.py` and `export_cad.py` (they drive a local Chrome and
a local web server, which a gateway must not do), the plugin's commands,
agents and examples, and its `dev/` patch history.

The Python under `formgraph/` is kept close to the plugin so the two can be
diffed, so it is excluded from ruff and mypy (`pyproject.toml`).

## three.js

`engine/vendor/` holds three.js **0.128.0** (r128, MIT, `LICENSE.three`): the
same version the plugin loaded from a CDN, kept because the engine uses the
classic `examples/js` scripts, which three.js stopped publishing after r147.
`MANIFEST.json` records the npm tarball's integrity and each file's sha256;
`tests/unit/test_imagine.py` checks them.

| File | sha256 |
|---|---|
| `three.min.js` | `9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2` |
| `OrbitControls.js` | `02bb4ade710f3e607329e37a21f098bc3ac70eb6e33daf8a65e79f4db785e7b2` |
| `GLTFExporter.js` | `a228cae09518e5034600a1aec65c3b3453f706f8943e542721c63c5e5727499c` |

## Layout

- `schemas.py`: the contract's request and response shapes.
- `engine_page.py`: config validation, the embedding, assets and the CSP.
- `service.py`: the studio item, events, the handover checklist, handover and rework.
- `twin_bridge.py`: every twin write, through the twin's own recorders.
- `routes.py`: the REST routes.
