# Operations and the project stream

An **operation** is one unit of work a person started and may want to watch:
a design-flow run, a slow tool job, or a Create chat turn. Every operation in
a project reports its progress as a sequence of **operation events**. The
gateway keeps those events in a durable log and streams them to the Create and
Engineering views over one Server-Sent Events stream per project, so a browser
that refreshes, navigates away and back, or reconnects after a gateway restart
is sent what it missed instead of losing it (FORGE-785, the backend of CDE
slice 1; FRS sections 11B and 14).

The code lives in `api_gateway/operations/`: `schemas.py` (the contract),
`store.py` (the SQLite log and snapshots), `hub.py` (the stream),
`publisher.py` (what producers call) and `routes.py`.

## Identity

| Operation | `operation_id` | `kind` | Stages |
|-----------|----------------|--------|--------|
| A design-flow or plain run | `run:<run_id>` | `run` | the flow's phases |
| A slow tool job | `job:<job_id>` | `job` | none |
| A Create chat turn | `chat:<turn_id>` | `chat` | none |

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
  refused by the store and ignored by clients. A terminal event after a
  terminal one is accepted (a completed result can later go `stale`).
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
message, severity, artifact_refs, requires_action, action_refs, progress` and
`stages: [{stage_id, stage_title, domain, state, updated_at}]`. It is folded
from the operation's events as they are stored: the latest event sets state,
message, severity, progress and the actions on offer; artifacts accumulate;
stages are upserted by `stage_id`; when the operation ends, any stage still in
flight ends with it. A stage's `domain` is its lane; the operation keeps the
domain of its own events.

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
cancelled, `409` when it cannot be, `404` when it is unknown. What can be
cancelled today:

| Operation | Cancellable |
|-----------|-------------|
| A plain run, not yet ended | yes |
| A design-flow run | no (409): reject it at its next gate |
| A job running in the gateway | yes, through `JobRunner.cancel` |
| A job running in the MCP sidecar | no (409) |
| A chat turn | no (409) |

The cancelled event itself comes from the run or job producer, as for any
other transition.

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

All four routes sit behind the gateway's authentication middleware like every
other route. Per-project access control is FORGE-787.

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
concept). When a job's result has a `node_id`, it is an `artifact_ref`. Jobs
gained two statuses: `cancelled` (`JobRunner.cancel`) and `superseded`
(`JobRunner.supersede`); both end the job at once and stop its task. A job the
store marks `interrupted` on opening is reported `failed`.

**Approvals (RT-030).** An approval item carries `base_revision` where the
revision of what it is about is known (today a design sketch's or technical
drawing's content hash). A decision may send the `base_revision` it saw; when
the item has moved on since, the decision is refused with 409 and nothing is
recorded. A decision without one, or on an item that tracks none, is
unaffected.

## Storage and configuration

| Setting | Default | Meaning |
|---------|---------|---------|
| `METAFORGE_OPERATIONS_PATH` | `~/.metaforge/operations.db` | The SQLite log (on the `metaforge-home` volume in compose) |
| `METAFORGE_OPERATIONS_RETENTION_DAYS` | `14` | Events older than this are pruned; `0` keeps everything |

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

Alerts in `observability/alerting/rules.yaml`: `OperationEventPublishFailing`
(the store keeps failing to write) and `OperationsStalled` (operations are
open and none has reported anything for an hour). Log events:
`operation_event_published`, `operation_event_refused`,
`operation_event_publish_failed`, `operation_stream_resync`,
`operation_stream_closed`. Spans: `operations.publish`, `operations.list`,
`operations.cancel`, `operations.stream.replay`.

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
  control is FORGE-787.
- **No ETA.** `progress` only when a measurable total exists (for example
  checks done of total). A tool's static `expected_seconds` is never shown as
  an ETA.
