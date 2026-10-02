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

## Install as a plugin

The MCP config below is the manual route. There is also a generated plugin
that brings the MCP server, the 29 engineering skills and the slash
commands in one install:

```
/plugin marketplace add FidelOdok/MetaForge
/plugin install metaforge
```

Claude Code asks for your **gateway URL** the first time you enable it. The
default suits a gateway on your own machine; a team or hosted gateway is the
same package with a different URL — the endpoint is a value you supply, not
something compiled into the package. An API token field is there for a
gateway that needs one; a local gateway started without auth does not.

### Commands

| Command | What it does |
|---|---|
| `/metaforge:use` | Pick the project for this session and summarise where it stands |
| `/metaforge:status` | What is built, and which requirements are unverified |
| `/metaforge:design` | Design or revise a part, committed to the twin |
| `/metaforge:fea` | Run a load case and record the evidence against the revision it came from |
| `/metaforge:gate` | Review whether the project can be promoted |
| `/metaforge:doctor` | Check the connection, and what is unreachable |

### Regenerating the package

`integrations/claude-code/` is build output. Edit the generator, not the
output:

```bash
python scripts/build_integrations.py
claude plugin validate --strict integrations/claude-code
```

Skill frontmatter is generated from each skill's `definition.json` rather
than written into the source `SKILL.md` files. Those files are read as raw
body text and injected as procedural context, so frontmatter added there
would appear in the model's prompt as part of the procedure.

## Keeping the tool list a size Claude Code can use

All 97 tools are served by default. If that is more than you want in
context, start the server with a profile:

```bash
python -m metaforge.mcp --transport stdio --profile mechanical
```

`core`, `mechanical`, `simulation`, `electronics` and `robotics` are each
between 20 and 40 tools. An unknown name stops the server rather than
quietly serving everything.

Whatever the reason a tool is not in the list, the response says so.
An adapter whose container is down contributes nothing, but appears under
`_meta.unavailableAdapters`; a profile naming a tool no loaded adapter
registers appears under `_meta.profile.missing`. `_meta` is absent when
there is nothing to report, so seeing it at all means something is worth
looking at — `python -m metaforge.mcp --transport stdio` with no
adapters down and no profile set returns none.

## Writes wait for a human

A tool that writes is held for approval when Claude Code reaches the
gateway as a remote caller. The check runs on the server's dispatch path,
so it applies whatever client asks and cannot be skipped by a client that
does not implement it.

A held call appears on the dashboard's **Approvals** page, tagged with
the caller and `source: mcp`. Approve it there and the tool runs;
reject it and it does not.

When Claude Code declared the `elicitation` capability at `initialize`, the
question is asked inside Claude Code instead, and the dashboard is only the
fallback. Over HTTP it travels on whichever stream the session has: a
`GET /mcp` stream if one is open, otherwise the held `tools/call`'s own
response, which the server switches to `text/event-stream` when the call
was sent with `Accept: application/json, text/event-stream` (FORGE-464). Claude Code never opens `GET /mcp`, so this second path is the one it uses.
The answer comes back as a POST and the tool result follows on the same
stream. See
[Over HTTP: which stream carries the question](../capability-matrix.md#over-http-which-stream-carries-the-question).

### When an inline question expires

The inline prompt follows the same window rule as a dashboard hold (see the
table below, FORGE-472). Claude Code sends no `progressToken`, so the window
is `METAFORGE_APPROVAL_HOLD_SECONDS` (default 100s), which ends before
Claude Code's own 120s tool timeout. The prompt says how long the person
has ("Answer within 100 seconds. After that this request expires and
project.create is not run.").

When the server stops waiting on a question for any reason (the window
ended, the call was cancelled, or its stream closed), it sends
`notifications/cancelled` naming the `elicitation/create` request id, so the
client can take the form down:

```json
{"jsonrpc": "2.0", "method": "notifications/cancelled",
 "params": {"requestId": "elicit-<session>-1", "reason": "approval window ended"}}
```

It travels on the call's own stream while that stream is open, ahead of
the call's `timed_out` result, or on an open `GET /mcp` stream otherwise.
An answer that arrives after that is acknowledged `202`, logged as
`mcp_elicitation_late_response_ignored`, and never applied: the call it
would have approved has already ended without running.

### While a call waits on the dashboard

A call held for the dashboard is not silent (FORGE-465). If the request
carried a `progressToken` (`params._meta.progressToken`) and the server has
a way to reach the client for that call, it sends
`notifications/progress` as soon as the hold exists. The message names the
approval id and where to answer it, with a link to the Approvals page when
`METAFORGE_DASHBOARD_URL` is set:

```json
{"jsonrpc": "2.0", "method": "notifications/progress",
 "params": {"progressToken": "t1", "progress": 0, "total": 180,
            "message": "project.create is held for human approval (approval run_b42aa3ea023f46c0). A person must approve or reject it on the MetaForge dashboard Approvals page: http://localhost:3000/approvals. Waiting up to 180s."}}
```

The same notification repeats every 10s while the call waits, with
`progress` increasing, so a client that resets its tool timeout on progress
waits for the person rather than giving up on them. Over HTTP the
notification travels on the call's own response stream, so the POST needs
`Accept: application/json, text/event-stream`; this works whether or not the
client can elicit. A call that is never held keeps its plain JSON response.

How long a held call waits depends on that channel, because without
progress nothing resets the client's own tool timeout (120s in Claude Code).
The rule is the same whether the question is on the dashboard or inline:

| Situation | Window | Variable |
|-----------|--------|----------|
| progress is being sent | 180s | `METAFORGE_APPROVAL_HOLD_PROGRESS_SECONDS` |
| no progress channel (no `progressToken`, or no stream to carry it) | 100s | `METAFORGE_APPROVAL_HOLD_SECONDS` |

`METAFORGE_APPROVAL_PROGRESS_INTERVAL_SECONDS` sets the repeat interval.
The chosen window is what the hold's ledger deadline is computed from, and
the server logs it with the route on `mcp_tool_call_held_for_approval`
(`route`, `window_seconds`, `progress`). An invalid value is logged as
`approval_hold_env_invalid` and the default is used.

### When it is not approved

The call comes back as a JSON-RPC error naming the outcome, the approval
id and where it was waiting, so the agent can say "held for approval
`run_...`, nobody approved within 100s" rather than report a bare timeout:

```json
{
  "code": -32001,
  "message": "project.create was not run (timed_out): held for approval run_b42aa3ea023f46c0 on the MetaForge dashboard Approvals page (http://localhost:3000/approvals), and no one answered before the approval window closed (100s).",
  "data": {"tool_id": "project.create", "code": "approval_required",
            "outcome": "timed_out", "approval_id": "run_b42aa3ea023f46c0",
            "route": "dashboard", "where": "the MetaForge dashboard Approvals page (http://localhost:3000/approvals)",
            "held_seconds": 100.02, "window_seconds": 100.0, "retryable": false}
}
```

`outcome` is `rejected` (a reviewer said no), `timed_out` (nobody answered
within the window), `cancelled` (the hold was closed before anyone
answered) or `not_configured` (the server holds writes but has no approval
gate wired, so it refused rather than ran). None is worth retrying without
a person doing something first. An approved call carries the matching
FORGE-417 note instead: `This write was held for human approval and was
approved by ... (approval run_...) after 12.3s via dashboard.`

A held call that stops waiting takes its entry off the **Approvals** page
with it (FORGE-466). When its window closes the entry is marked
`timed_out`; when the call is cancelled (your client disconnected, or the
server shut down) it is marked `canceled`. If the server dies before it can
say so, the gateway expires the entry itself 30s after the window it was
given. Approving or rejecting an entry in either state returns `409` with
the reason and records nothing, so a late click can never approve a call
that is no longer running. Ask for the call again if it is still wanted.

Running over **stdio on your own machine**, writes are not held by
default: there is nowhere to answer an approval in that transport yet.
That changes when a deployment sets `exempt_local_writes=False`.

## Design flows from Claude Code

The sidecar serves the design-flow tools, and they are in the `core`
profile the plugin installs by default (FORGE-462):

| Tool | |
| --- | --- |
| `flow.list` | every launchable flow, with its phases and gates (read) |
| `flow.propose` | tailor a template to an intent and hold it for a person, or return `needs_input` questions to ask the user first (held write) |
| `flow.start_run` | start a run on a flow version a person has **approved** (held write) |
| `flow.status` | phase-by-phase state of one run (read) |
| `run.start_design_flow` | start one of the built-in flows on a goal (held write) |
| `run.get_status` | a run's state, gate reason and result (read) |

They act on the gateway's state, not the sidecar's: a flow proposed here
is the one on the dashboard's **Approvals** page, and a run started here
is the one in `GET /v1/runs`. That needs `METAFORGE_GATEWAY_URL` on the
sidecar (the dev compose file sets it to `http://gateway:8000`). Unset,
the tools still register but bind to the sidecar's own in-process stores,
which the dashboard cannot see, and the sidecar logs
`mcp_flow_bindings_in_process` on start-up to say so.

The loop is propose, a person approves, then start. `flow.start_run`
right after `flow.propose` is refused with a message saying the version
is not approved yet; that is the expected answer, not a fault.

## Tool annotations and unknown-tool errors

Every tool on `tools/list` carries the MCP annotation hints, so Claude Code
can tell a read from a write before it calls anything:

```json
{
  "name": "twin.get_node",
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  }
}
```

The classification is deliberate rather than derived from the tool's
name, and a tool nobody has classified inherits MCP's safe defaults
(`readOnlyHint: false`, `destructiveHint: true`). One case is worth
knowing: `twin.query_cypher` is read-only until the gateway is started
with `--allow-twin-mutations`, after which it is not — the hint is
computed per request from that flag, so it always matches what the
server will enforce.

Getting a tool id slightly wrong is recoverable. Separator mistakes
resolve on their own, so `twin_get_node` and `twin/get_node` both reach
`twin.get_node`; an alias that matches two tools is refused rather than
guessed at. When an id matches nothing, the error names the closest real
tools, in the message and in `error.data.did_you_mean`:

```json
{
  "code": -32601,
  "message": "Tool not found: twin.get_nodes. Did you mean: twin.get_node",
  "data": {
    "tool_id": "twin.get_nodes",
    "did_you_mean": ["twin.get_node"],
    "tool_count": 97
  }
}
```

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
* No `flow.*` or `run.*` tools: the sidecar image predates FORGE-462,
  or the adapters are switched off with
  `METAFORGE_ADAPTER_DESIGN_FLOW_ENABLED=false` /
  `METAFORGE_ADAPTER_RUN_ENABLED=false` (or a `METAFORGE_ADAPTERS` list
  that omits `design_flow` / `run`).

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
