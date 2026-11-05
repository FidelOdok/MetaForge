# Operations and the project stream

An **operation** is one unit of work a person started and may want to watch:
a design-flow run, a slow tool job, or a Create chat turn. Every operation in
a project reports its progress as a sequence of **operation events**. The
gateway keeps those events in a durable log and streams them to the Create and
Engineering views over one Server-Sent Events stream per project, so a browser
that refreshes, navigates away and back, or reconnects after a gateway restart
is sent what it missed instead of losing it (the backend of CDE
slice 1; FRS sections 11B and 14). CDE slice 2 adds scheduling: cancel and
retry, supersede, coalescing and stale-result rejection, so a late result can
never overwrite a newer revision (see [Scheduling](#scheduling)).

The code lives in `api_gateway/operations/`: `schemas.py` (the contract),
`store.py` (the SQLite log and snapshots), `hub.py` (the stream),
`publisher.py` (what producers call), `scheduling.py` (cancel and retry),
`heads.py` (a target's current head), `sidecar.py` (the relay to the MCP
sidecar) and `routes.py`.

## Identity

| Operation | `operation_id` | `kind` | Stages |
|-----------|----------------|--------|--------|
| A design-flow or plain run | `run:<run_id>` | `run` | the flow's phases |
| A slow tool job | `job:<job_id>` | `job` | none |
| A Create chat turn | `chat:<turn_id>` | `chat` | none |
| A sketch save from the Create stage | `job:sketch-<save_id>` | `job` (domain `sketch`) | none |
| A sketch plane change (confirm, reassign, undo, archive) | `job:sketch-plane-<op_id>` | `job` (domain `sketch`) | none |
| Making a sketch editable: the stored proposal, its promotion and sign-off | `job:sketch-compile-<interpretation_id>` | `job` (domain `sketch`) | `interpretation_proposed`, `parametric_review` |
| Generating a concept sketch (`twin.generate_sketch` or Create **Generate**) | `job:<job_id>` | `job` (domain `sketch`) | a milestone when the image arrives, a preservation `review_required` |
| Two authoritative references that disagree | `job:reference-conflict-<key>` | `job` (domain `concept`) | `review_required` naming both citations |
| What a design change affects across domains | `job:impact-<impact_id>` | `job` (domain `cross_domain`) | `dependencies_invalidated` |

Approvals are not operations. A run waiting at a gate emits a
`review_required` event whose `action_refs` name the approval to decide
(`gate:<run_id>` through the [unified approvals API](approvals.md)).

## OperationEvent

```json
{
  "event_id": "uuid",
  "project_id": "string",
  "project_seq": 123,
  "operation_id": "run:abc | job:def | chat:ghi",
  "sequence": 7,
  "run_id": "string | null",
  "kind": "run | job | chat",
  "title": "Concept design",
  "domain": "concept | sketch | mechanical | electronics | firmware | simulation | manufacturing | cross_domain | general",
  "stage_id": "string | null",
  "stage_title": "string | null",
  "type": "accepted | queued | started | milestone | partial_result | preview_ready | review_required | blocked | resumed | completed | failed | cancelled | superseded | stale",
  "state": "queued | running | waiting_for_user | blocked | completed | failed | cancelled | superseded | stale",
  "occurred_at": "ISO-8601 UTC",
  "base_revision": "string | null",
  "target_revision": "string | null",
  "target_key": "string | null",
  "retry_of": "string | null",
  "message": "server-authored plain text, never prompts or tool args",
  "severity": "info | warning | error",
  "artifact_refs": [{"kind": "work_product | file | glb | image | step", "id": "string", "label": "string", "url": "string | null", "authority": "illustrative | preview | exact | validated | null"}],
  "requires_action": false,
  "action_refs": [{"kind": "approval | retry | cancel | open", "id": "string", "label": "string"}],
  "progress": {"done": 3, "total": 8, "unit": "checks"}
}
```

Rules:

- `project_seq` is allocated per project, is monotonic and survives a restart.
  It is the SSE `id:`. It is never reused, even after old events are pruned.
- `sequence` is per operation and monotonic. A client drops any event whose
  `sequence` is at or below the last one it applied for that operation, and
  the store refuses one.
- Terminal states are `completed`, `failed`, `cancelled`, `superseded` and
  `stale`. Once an operation is terminal, a later non-terminal event is
  refused by the store and ignored by clients. Of the terminal events, only
  `stale` may follow an ending, and only a `completed` one (a completed result
  can later go stale). Any other event after an ending is refused, so a late
  result of a cancelled or superseded operation never becomes its outcome.
- `target_key` names what the operation works on (an item key such as
  `CAD-BRACKET`) and `base_revision` the revision of it the work started
  from. Both are optional; see [Scheduling](#scheduling).
- `retry_of` is set on an operation started by retrying a failed one, and
  names it.
- `progress` is null unless a measurable total exists. There is no ETA.
- An artifact claims an `authority` only when evidence exists; a preview is
  never `validated`.
- `message` is written by the gateway from a fixed set of phrases. It never
  carries a prompt, a tool's arguments or results, or a worker's free text.

## REST and SSE

### `GET /v1/projects/{project_id}/operations?active=true|false&limit=50`

```json
{"project_id": "p1", "cursor": 42, "operations": [OperationSnapshot]}
```

`active=true` lists only operations that are not terminal; the default lists
every operation still kept, most recently updated first. `cursor` is the
project's latest `project_seq`: open the stream with `?after=<cursor>` to get
every event after this snapshot.

An `OperationSnapshot` has `operation_id, kind, domain, title, state, run_id,
started_at, updated_at, last_sequence, base_revision, target_revision,
target_key, retry_of, message, severity, artifact_refs, requires_action,
action_refs, progress` and
`stages: [{stage_id, stage_title, domain, state, updated_at}]`. It is folded
from the operation's events as they are stored: the latest event sets state,
message, severity, progress and the actions on offer; artifacts accumulate;
stages are upserted by `stage_id`; when the operation ends, any stage still in
flight ends with it. A stage's `domain` is its lane; the operation keeps the
domain of its own events. `started_at` is the time of the first event that is
not `queued`, so an operation that opens straight at a decision has one too;
it is `null` only while the operation is still queued.

### `GET /v1/projects/{project_id}/operations/stream`

SSE, resumable from the `Last-Event-ID` header or `?after=<seq>`. Without
either, it streams only what happens from now on. It replays from the durable
store, then streams live. Frames:

| Frame | `id:` | Data |
|-------|-------|------|
| `event: operation` | the `project_seq` | an OperationEvent |
| `event: heartbeat` (every 15 s) | none | `{"server_time", "cursor"}` |
| `event: resync` | none | `{"reason", "cursor"}` |

`resync` is sent when the resume point is ahead of the project's log
(`unknown_cursor`) or older than what the log keeps (`cursor_expired`). The
client refetches the snapshot and resumes from the `cursor` given. A slow
subscriber whose queue fills is not dropped: the stream reads what it missed
back from the store. The response carries the same no-buffering headers as
the run stream (`Cache-Control: no-cache, no-transform`, `X-Accel-Buffering:
no`) and a padding comment first. The dashboard reads it with its fetch-based
SSE reader, which can send a bearer token.

### `POST /v1/projects/{project_id}/operations/{operation_id}/cancel`

`202 {"operation_id", "state": "cancelled"}` when the operation was
cancelled, `409` when it cannot be, `404` when it is unknown, `503` when the
process that runs it could not be reached. What can be cancelled:

| Operation | Cancellable |
|-----------|-------------|
| A plain run, not yet ended | yes, through the run state machine |
| A design-flow run, not yet ended | yes: its Temporal workflow is cancelled (or the in-process executor stops at its next step), then the run is marked cancelled |
| A job running in the gateway | yes, through `JobRunner.cancel` |
| A job running in the MCP sidecar | yes, relayed to the sidecar's `POST /v1/jobs/{job_id}/cancel` |
| A chat turn | no (409) |

Cancelling never rolls back the twin. A design-flow run's open drafts are
closed as on any other ending, and whatever it already committed at an
approved gate stays committed. When Temporal cannot be reached the run is not
marked cancelled (503): a record that says cancelled while its workflow keeps
spending would be wrong.

The cancelled event itself comes from the run or job producer, as for any
other transition. For a sidecar job the gateway also records it at once, with
the same `event_id` and `sequence` the sidecar's runner uses, so the sidecar's
own report is a duplicate.

An operation offers a `cancel` action only while it can be cancelled, and a
`retry` action only on a failure that can be retried. The dashboard shows
exactly the actions listed.

### `POST /v1/projects/{project_id}/operations/{operation_id}/retry`

Runs a failed operation again, with the same request, as a new operation.
`202 {"operation_id": "<the new one>", "retry_of": "<the failed one>",
"state"}`; the new operation's first event is `accepted` and carries
`retry_of`. `409` when the operation did not fail or cannot be re-run
safely, `404` when it is unknown, `503` when the sidecar could not be
reached.

| Operation | Retryable |
|-----------|-----------|
| A failed design-flow run | yes: the same request starts a new run (its own change set; the failed run's drafts were already closed) |
| A failed plain run | no (409): a chat turn or a held tool call cannot be started again safely |
| A failed or interrupted job | yes, when its tool registered a retry (`twin.generate_concept_mesh`, `twin.build_exact_cad`, `twin.export_cad_step`): the same arguments, session, project, target and base revision, as a new job |
| A failed job in the MCP sidecar | yes, relayed to the sidecar's `POST /v1/jobs/{job_id}/retry` |
| A chat turn | no (409): send the message again |

A retry with a stale `base_revision` ends `stale` straight away, before any
work is paid for: it is honest that the design moved on.

### `POST /v1/projects/{project_id}/operations/events`

Ingest for a process that has no stream of its own (the MCP sidecar, the
design-flow worker). The body is an OperationEvent without `project_seq`;
`event_id` is optional and makes a retried post idempotent; `sequence` is
optional and, when left out, the store gives the event the next one for its
operation. A `project_id` in the body must match the path.

| Outcome | Status | Body |
|---------|--------|------|
| Stored | 202 | `{"event_id", "project_seq", "sequence", "duplicate": false}` |
| Duplicate `event_id` | 200 | the seq stored the first time, `"duplicate": true` |
| Out of order, or after the operation ended | 409 | the reason |

A `completed` event naming a `target_key` and `base_revision` that is no
longer the target's head is stored as `stale` (see
[Scheduling](#scheduling)).

All five routes sit behind the gateway's authentication middleware like every
other route. Per-project access control is planned separately.

## Producers

Each producer emits real transitions only: no timers and no invented
progress.

**Runs** (`api_gateway/runs/operation_events.py`), from the run store's
transition callback and the run's tool events:

| Transition | Event |
|------------|-------|
| The run is created | `accepted`, then `queued` |
| `queued` to `running` | `started` |
| `awaiting_approval` to `running` | `resumed` |
| `awaiting_approval` | `review_required`, `requires_action`, an `approval` action |
| The first tool call of a phase | `milestone`, with the phase as the stage, now `running` |
| `completed` | `completed` |
| `failed`, `timed_out` | `failed` |
| `canceled`, `rejected` | `cancelled` |
| Refused before it started | `failed` |

While a run has not ended its events offer `cancel`; a failed design flow's
`failed` event offers `retry`. A run started by a retry names the failed run
in `retry_of`.

The run's `title` is its flow's name and its `domain` comes from the flow id;
a stage's domain comes from the phase's disciplines, its id or the tool. A run
with no project has nowhere to report until it has one. The run stream
`/v1/runs/{id}/events` is unchanged: it still sends `run.status` and
`tool.progress`.

**Jobs** (`tool_registry/jobs.py`): every state change of a job that belongs
to a project goes to a publisher injected into `JobRunner`. The default is
the process seam in `mcp_core/operation_events.py`: inside the gateway it
writes to the log, in the sidecar it posts to the ingest route. Each step has
a fixed `sequence` (queued 10, running 20, every ending 90), so events stay in
order however they travel, and a deterministic `event_id`, so a retried post
is a duplicate. The domain comes from the tool id prefix (`kicad.` is
electronics, `cadquery.` mechanical, `calculix.` simulation, a concept tool
concept, a `twin.*_cad*` tool mechanical). When a job's result has a `node_id`, it is an `artifact_ref`. Jobs
gained three statuses: `cancelled` (`JobRunner.cancel`), `superseded`
(`JobRunner.supersede`) and `stale` (see [Scheduling](#scheduling)); each ends
the job at once and stops its task. A job the store marks `interrupted` on
opening is reported `failed`. A job's events carry its `target_key`,
`base_revision` and `retry_of` when it has them; an active job offers
`cancel`, and a failed job of a tool that can be retried offers `retry`.

**Chat turns** (`api_gateway/chat/operation_events.py`). A person's message
in a project thread (`POST /v1/chat/threads/{id}/messages`) is one operation,
`chat:<turn id>`:

| Moment | Event |
|--------|-------|
| The message is stored, before any agent work | `accepted` |
| The agent starts the turn | `started` |
| The agent replied (or no agent is set up) | `completed` |
| The reply is an error | `failed` |
| The page went away and the turn was stopped | `cancelled` |

The turn id is the request's optional `client_turn_id` when it is well formed
and not yet used in the project, else the stored message's id. The Create
view sends its instruction's id, so it knows the operation before the reply
returns and shows the server's acknowledgement as soon as `accepted`
arrives. The response carries the turn's `operation_id`. The messages are
fixed phrases; the prompt and the reply never appear on the stream. A thread
with no project reports nothing. A chat turn offers neither cancel nor
retry.

**Approvals (RT-030).** An approval item carries `base_revision` where the
revision of what it is about is known:

| Approval | `base_revision` |
|----------|-----------------|
| A design sketch or technical drawing | the content hash |
| A design-flow gate | `gate:<digest>` of the gate's phase and attempt and the run's open drafts (each item's key, draft revision and the base revision it was drafted on) |
| A design change proposal | `wp:<digest>` of the content of every work product it affects |

A decision may send the `base_revision` it saw, on
`POST /v1/approvals/{id}/decision` or on `POST /v1/runs/{id}/approval`. When
the item has moved on since (the run re-drafted, retried or reached another
gate; an affected work product changed), the decision is refused with 409 and
nothing is recorded. A decision without one, or on an item that tracks none,
is unaffected. The dashboard's approval cards send the revision they show.

## Cross-domain impact {#cross-domain-impact}

When an item's head moves and the change reaches
other items through their interface edges, the twin records the impact (see
[interface edges and cross-domain impact](twin_schema.md#interface-edges-and-cross-domain-impact))
and publishes it as one operation, `job:impact-<impact_id>`, domain
`cross_domain`. It is published through the same process seam as tool jobs, so
a head moved in the MCP sidecar reports here too.

A head move that reaches nothing (no affected item, no stale result, no
proposed re-check and no review action) records no impact and opens no
operation, so the stream never shows an impact job that has nothing to say.

| Moment | Event |
|--------|-------|
| The impact is recorded | `accepted` |
| Dependencies invalidated | `milestone` with `stage_id: dependencies_invalidated`; `artifact_refs` name the affected items and the results now out of date; `severity: warning` when any result went stale |
| A re-check waits for a person | `review_required`, `requires_action`, one `approval` action per waiting re-check (`rerun:<impact_id>:<rerun_id>`) |
| Nothing waits, or the last waiting re-check is decided | `completed` |

`dependencies_invalidated` is not a contract type of its own: it is a
`milestone` with that stage, so every client already renders it. Each step has
a fixed `sequence` and a deterministic `event_id`, so a retried publish is one
event. The operation carries `base_revision` and `target_revision` (`KEY@1`,
`KEY@2`) but no `target_key`, so it never supersedes or stale-screens a real
job on that item. The messages are fixed phrases plus the impact's own plain
summary.

## Scheduling

Several operations can work on the same thing. These rules keep the design's
current state honest while they do (FRS DI-003, DEF-008, PERF-003 and
PERF-004).

**Targets.** A job may name a `target_key` (what it works on, usually an
item key) and the `base_revision` of it the work starts from.
`twin.generate_concept_mesh` takes both as optional arguments. An item's
revision here is the twin's own `head_revision`, the number its `KEY@n` pins
and change-set bases already compare (`api_gateway/operations/heads.py`).

**Supersede.** A new job on a target ends the older jobs still open on the
same target in the same project as `superseded`, and stops them. The job
runner does this when it starts the new job; the gateway's publisher does the
same for any open job operation it holds on that target, relaying to the
sidecar when the sidecar runs it. Only jobs are superseded: a run has gates
and drafts of its own and is never ended because something else started.
Once superseded, a late result from that job is refused by the store.

**Stale results.** A result whose `base_revision` is no longer the target's
head never moves the head:

- the twin refuses a write pinned to a base that moved (`KEY@n` raises
  `ItemRevisionConflictError`, a change set raises `ChangeSetConflictError`);
  a job whose work fails that way ends `stale`, not `failed`;
- the job runner reads the head before the work starts (a job whose target
  already moved ends `stale` and nothing is paid for) and again when its
  result arrives (the result is kept on the job record, never adopted);
- the gateway checks every `completed` event that names a target and base,
  from any process, and stores it as `stale` when the head moved.

A stale operation says which revision it worked from and which is current,
and the last-known-good revision stays the head.

**Coalescing.** An identical call (same tool, arguments, project, target and
base revision) from any session within `METAFORGE_JOB_COALESCE_WINDOW_S`
(default 30 s; `0` turns it off) returns the job already covering it instead
of starting another, so a double click or a retried request is idempotent. The
same session's identical call is reused for longer, as before (a running job,
or one that succeeded in the last ten minutes).

**The sidecar relay.** A job runs in the process it was called on, so a job
called through `mcp-http` is cancelled or retried there. The sidecar serves
`POST /v1/jobs/{job_id}/cancel` and `POST /v1/jobs/{job_id}/retry` with the
same credential as `/mcp`; the gateway calls them at `METAFORGE_MCP_URL` with
`METAFORGE_MCP_CLIENT_KEY` (else `METAFORGE_MCP_API_KEY`). With no
`METAFORGE_MCP_URL` such a job is not cancellable from the gateway (409).
Best effort: an unreachable sidecar is a 503, never retried.

## Exact CAD and STEP events

The exact CAD jobs (`twin.build_exact_cad`, `twin.export_cad_step`, see
[the twin schema](twin_schema.md#exact-cad-validity-and-step)) report on their
own `job:<job_id>` operation with `domain: mechanical`. The FRS section 14
names map onto the contract types:

| FRS name | `type` | `sequence` | When | Artifact authority |
|----------|--------|------------|------|--------------------|
| `exact_geometry_ready` | `milestone` (stage `exact_geometry_ready`) | 30 | the exact B-Rep was built | `exact` |
| `artifact_ready` | `partial_result` (stage `artifact_ready`) | 40 | a validated revision was committed, or a STEP was written | `validated` (a STEP only when its round trip passed; else `exact`) |
| `validation_completed` | `completed` | 90 | the job ended with a verdict: "Geometry validated", or built with warnings and not validated | `validated` only for a validated revision |
| `failed` | `failed` | 90 | the validity check failed or could not run ("the last valid revision was kept"), or the STEP export failed; offers `retry` | none |
| `stale` | `stale` | 95 | the head moved past the revision a completed STEP export belongs to | `exact` |

A preview never claims `validated`. A build job's own commit is its result:
the job runner does not call it stale because the head moved to the
revision the job itself wrote (the result names it as `target_revision`). A
build whose base moved before its pinned commit is refused by the twin and
ends `stale`, and the newer head is kept. A STEP export job's `target_key`
is `STEP:<KEY>@<n>`, not the item key, so a later build never supersedes it.

## Storage and configuration

| Setting | Default | Meaning |
|---------|---------|---------|
| `METAFORGE_OPERATIONS_PATH` | `~/.metaforge/operations.db` | The SQLite log (on the `metaforge-home` volume in compose) |
| `METAFORGE_OPERATIONS_RETENTION_DAYS` | `14` | Events older than this are pruned; `0` keeps everything |
| `METAFORGE_JOB_COALESCE_WINDOW_S` | `30` | Seconds an identical job call from any session joins the open job; `0` turns it off |
| `METAFORGE_MCP_URL` | unset (compose: `http://mcp-http:8765/mcp`) | Where the gateway relays cancel and retry for sidecar jobs |
| `METAFORGE_MCP_CLIENT_KEY` | unset | The key the relay presents (else `METAFORGE_MCP_API_KEY`) |

The store opens at gateway start-up beside the run ledger, and is skipped with
it when `METAFORGE_RUNS_LEDGER_DISABLE` is on (the stream then lives in
memory). Two tables: `operation_events` is append-only and allocates
`project_seq` from `operation_cursors` in the same transaction;
`operations` holds each operation's latest snapshot. Snapshots of operations
that ended before the retention window are pruned with their events.

## Observability

| Metric | Type | Labels |
|--------|------|--------|
| `metaforge_operation_event_total` | counter | `kind`, `type` |
| `metaforge_operation_event_publish_failure_total` | counter | `kind`, `reason` (`error`, `out_of_order`, `after_terminal`) |
| `metaforge_operation_stream_replay_total` | counter | none |
| `metaforge_operation_stream_resync_total` | counter | `reason` |
| `metaforge_operation_stream_active` | gauge | none |
| `metaforge_operation_active` | gauge | `kind` |
| `metaforge_operation_event_publish_lag_seconds` | histogram | `kind` |
| `metaforge_operation_scheduling_total` | counter | `kind`, `action` (`superseded`, `stale_rejected`, `coalesced`, `cancel`, `retry`), `outcome` (`ok`, `refused`, `error`) |

Alerts in `observability/alerting/rules.yaml`: `OperationEventPublishFailing`
(the store keeps failing to write), `OperationsStalled` (operations are
open and none has reported anything for an hour) and
`OperationCancelOrRetryFailing` (cancel or retry keeps failing because the
workflow engine or the sidecar cannot be reached). Log events:
`operation_event_published`, `operation_event_refused`,
`operation_event_publish_failed`, `operation_stream_resync`,
`operation_stream_closed`, `operation_superseded`, `operation_result_stale`,
`operation_cancelled`, `operation_retried`, `operation_sidecar_relayed`,
`operation_sidecar_unreachable`, `tool_job_reused` (with `coalesced`),
`tool_job_retried`. Spans: `operations.publish`, `operations.list`,
`operations.cancel`, `operations.retry`, `operations.supersede`,
`operations.screen_result`, `operations.item_head`,
`operations.sidecar_relay`, `operations.stream.replay`,
`design_flow.cancel`.

## ADR: decisions

- **One operation per unit of work.** A design-flow run is one operation
  (`run:<run_id>`) and its phases are stages. A tool job is one operation
  (`job:<job_id>`). A Create chat turn is one operation (`chat:<turn_id>`).
  Approvals are not operations; they surface as `review_required` events on
  the operation they block, with `action_refs`.
- **Store.** SQLite in the gateway, like jobs and the run ledger. The sidecar
  and the flow worker publish over HTTP ingest, the same pattern as
  `/v1/twin/projects/{id}/live-events`. No Kafka is needed for this path.
- **Names.** The section 14 event names are used on the new project stream
  only. `/v1/runs/{id}/events` keeps `run.status` and `tool.progress`
  unchanged.
- **Source of truth.** Gateway transitions are the source of operation events
  for design flows, not Temporal history.
- **Access.** Existing principal authentication only; per-project access
  control is planned separately.
- **No ETA.** `progress` only when a measurable total exists (for example
  checks done of total). A tool's static `expected_seconds` is never shown as
  an ETA.
- **Stale is reported, the head is protected by the twin.** Slice 2 reuses
  the twin's existing conflict checks (`KEY@n` pins and change-set bases)
  rather than a new notion of "current"; the operation layer only reports
  what they decide, and checks results from processes it does not run.
- **Supersede jobs only.** Supersede ends jobs, whose result a newer one
  replaces outright. Runs are never superseded: cancelling one is a person's
  decision.
- **Retry is a new operation.** A retry never reopens the failed operation;
  it starts a new one that names it in `retry_of`, so the failure stays on
  record as it happened.
