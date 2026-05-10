# Use MetaForge from Claude Code

> **Status:** P1.11 Real Backends. Walkthrough for spawning the
> MetaForge MCP server as a Claude Code subprocess and driving its
> tools from inside a chat. Update when the launcher CLI grows new
> flags or transports.

Claude Code can spawn MCP servers as subprocesses and route tool
calls to them through stdio. This page walks you from a fresh clone
to "ingest a file and ask MetaForge about it" in five steps.

For a config reference (HTTP, SSE, auth shapes, reverse direction),
see [`mcp-config-examples.md`](mcp-config-examples.md).

## 1. Prerequisites

* Python 3.11+ on `PATH` as `python` (Claude Code spawns the
  subprocess with `command: "python"` — alias if needed).
* This repo cloned and editable-installed:

  ```bash
  git clone https://github.com/FidelOdok/MetaForge.git
  cd MetaForge
  python -m venv .venv && source .venv/bin/activate
  pip install -e ".[knowledge,cadquery]"
  ```

  Skip `cadquery` extras on systems where the CAD kernel won't build —
  the launcher gracefully drops adapters whose Python dep is missing
  (the manifest still lists them; only the handler skips).

* Optional but recommended: backend services for full functionality.

  ```bash
  docker compose up -d postgres neo4j
  ```

  Without these, the standalone server still boots — it just registers
  fewer adapters (no `knowledge.*` if Postgres+pgvector aren't reachable).

## 2. Place `.mcp.json`

**Project-level** (recommended for shared dev): the `.mcp.json` at
the repo root already points at the standalone launcher. Cloning the
repo and opening it in Claude Code is enough.

**User-level** (per-machine override): drop a `.mcp.json` in your
home directory (`~/.mcp.json` on macOS/Linux; see Claude Code docs
for Windows). User-level entries layer over project-level ones.

The repo's `.mcp.json`:

```json
{
  "mcpServers": {
    "metaforge": {
      "command": "python",
      "args": ["-m", "metaforge.mcp", "--transport", "stdio"],
      "env": {
        "METAFORGE_ADAPTERS": "cadquery,calculix,knowledge"
      }
    }
  }
}
```

Adjust `METAFORGE_ADAPTERS` to trim the loaded set (e.g.
`cadquery,calculix` if Postgres isn't running locally).

## 3. Launch Claude Code

```bash
cd /path/to/MetaForge
claude  # or your editor's Claude Code integration
```

Claude Code reads `.mcp.json` from the current working directory at
launch and spawns each entry as a subprocess. The MetaForge launcher
emits a `metaforge-mcp ready` line on stderr once adapters are
registered — you don't have to wait on it manually; Claude Code
handles the handshake.

## 4. Verify the connection

In the Claude Code chat, run:

```
/mcp
```

You should see `metaforge` listed as **connected** with one or more
tools available. A typical successful boot looks like this:

```
metaforge ✓ connected
  cadquery.create_parametric
  cadquery.boolean_operation
  cadquery.get_properties
  cadquery.export_geometry
  cadquery.execute_script
  cadquery.create_assembly
  cadquery.generate_enclosure
  calculix.run_fea
  calculix.run_thermal
  calculix.validate_mesh
  calculix.extract_results
  knowledge.search    (only when Postgres+pgvector reachable)
  knowledge.ingest    (likewise)
```

If `metaforge` shows **disconnected** or doesn't appear, jump to
[Troubleshooting](#troubleshooting).

## 5. Sample prompts

These are end-to-end exercises — each one routes through the
standalone launcher subprocess and exercises a different adapter
slice.

### Ingest a knowledge document

> Ingest the file `docs/architecture/system-vision.md` into MetaForge
> knowledge as a `design_decision`.

Claude Code will call `knowledge.ingest` with the file's contents.
The response carries `chunks_indexed` and a stable `source_path` you
can later search against. (Requires Postgres + pgvector running per
step 1.)

### Search the knowledge base

> Search MetaForge knowledge for "digital twin layers" — return the
> top 3 hits.

Routes through `knowledge.search`. You'll see snippets with their
`source_path`, similarity score, and (when set) `knowledge_type`.

### Generate a CAD part

> Use MetaForge to create a 50×30×10 mm CAD bracket and save it as
> `out/bracket.step`.

Routes through `cadquery.create_parametric` with
`shape_type=box, parameters={width:50, length:30, height:10},
output_path="out/bracket.step"`. The response carries `volume_mm3`,
`surface_area_mm2`, and the resolved `cad_file` path. Open the file
in any CAD viewer to confirm.

### Run a finite-element analysis

> Run a stress analysis on `out/bracket.step` with steel-316 material,
> 1 kN load on the top face, fixed base.

Routes through `calculix.run_fea`. Captures the run via the MET-331
`SimulationCapture` layer when wired into the gateway path.

## Troubleshooting

### `/mcp` shows `metaforge` as disconnected

Look at the Claude Code subprocess logs (Claude Code surfaces stderr
in its `/logs` view or via Settings → MCP Servers → View logs).
Common causes:

* **`python` not on PATH or wrong version.** The launcher requires
  Python 3.11+. Either alias `python` to your venv's binary, or edit
  `.mcp.json` to use `${VIRTUAL_ENV}/bin/python` explicitly.
* **`metaforge.mcp` not importable.** You haven't run `pip install -e
  .` in the active interpreter, or you're in a different venv than
  the one Claude Code spawned. Run `python -c 'import metaforge.mcp'`
  in the same shell that launches Claude Code to confirm.
* **Adapter import failures.** The launcher logs each adapter's
  registration outcome. Disable a problematic adapter with
  `METAFORGE_ADAPTER_<ID>_ENABLED=false` in the `.mcp.json` `env`
  block (e.g. `METAFORGE_ADAPTER_CADQUERY_ENABLED=false`).

### `tool/list` returns fewer tools than expected

The launcher is `--adapters`-aware. Either:

* Trim `METAFORGE_ADAPTERS` in `.mcp.json`, or
* Knowledge specifically requires a live `KnowledgeService` — if
  Postgres or pgvector aren't reachable, the knowledge adapter
  registers as a no-op and `knowledge.*` tools disappear from
  `tool/list`. Boot Postgres (`docker compose up -d postgres`) and
  restart Claude Code.

### Knowledge tools are listed but every search returns empty

Knowledge ingest is asynchronous in the gateway path but synchronous
in the standalone subprocess — every `knowledge.ingest` call commits
before returning. If searches still come back empty:

* Confirm the embedding model loaded (the launcher logs
  `embedder_warmup_complete` in stderr). First boot can take ~30s
  while sentence-transformers downloads weights.
* Check the `metaforge_retrieval_*` Prometheus histograms (MET-326)
  — if `recall@k` is 0 across every query, the corpus didn't
  populate.

### Auth failures (`auth_error`)

`METAFORGE_MCP_API_KEY` is set on the server but
`METAFORGE_MCP_CLIENT_KEY` doesn't match. Either unset both for
local-dev open mode or ensure the same value is in both env vars
(they can be set in the `.mcp.json` `env` block; the launcher
inherits the rest of the parent environment).

### Tool calls hang

The MET-306 hardened bridge enforces a per-call timeout. From outside
that path (e.g. directly from Claude Code), there's no client-side
deadline — set `--timeout` on the tool input where the schema
exposes it, or use the MCP harness's own per-call timeout setting.

## Related

* [`mcp-config-examples.md`](mcp-config-examples.md) — full config
  reference (stdio / HTTP / SSE / auth / reverse direction).
* `docs/integrations/codex.md` (MET-342) — Codex-specific
  walkthrough for HTTP/SSE clients.
* [MET-337](https://linear.app/metaforge/issue/MET-337) — standalone
  MCP server entrypoint.
* [MET-340](https://linear.app/metaforge/issue/MET-340) — automated
  end-to-end harness test that exercises this exact path on every
  CI run.
