# Unified approvals API

Every human approval MetaForge asks for is reachable through one API,
`/v1/approvals`. A dashboard queue, the CLI and an agent
all list, inspect and decide through the same three routes and read the same
normalized item, whatever the approval is about.

The older per-kind routes still work and are unchanged for callers. Both
surfaces call the same decision functions, so the 404, 409 and 422 rules are
one body of code.

In the dashboard, pending approvals are announced on every page, not only on
the Approvals page: the Approvals link in the sidebar shows how many are
waiting, and a notice opens when one arrives that has not been seen yet, with
the newest titles and a link to review them. Dismissing it hides those items
for the browser session; a new approval brings it back.

## Routes

| Route | Purpose |
|-------|---------|
| `GET /v1/approvals?status=pending\|decided\|all&project_id=<id>&kind=<kind>` | List items. `decided` is every item that is not pending (the audit view). |
| `GET /v1/approvals/{id}` | One item. |
| `POST /v1/approvals/{id}/decision` | Body `{"decision", "reason", "to_phase"}`. Returns the updated item. |
| `POST /v1/approvals/{id}/inline-decision` | A decision a person gave in the MCP client's own prompt, recorded by the sidecar that asked: a gate from `flow.await_gate`, or any pending item from `approvals.decide`. Body adds `approver` and `approver_verified`. See [Gates in the client chat](#gates-in-the-client-chat) and [Any approval in the client chat](#any-approval-in-the-client-chat). |

The list response is `{"items": [...], "unscoped_count": n}`. When scoped to a
project, `unscoped_count` is how many items were left out because they carry no
project, so a hidden item is reported rather than silently dropped.

## What is covered

The id is `<prefix>:<native id>`. The prefix says which store holds the item;
`kind` is finer.

| Id prefix | `kind` | Source | Decided by |
|-----------|--------|--------|------------|
| `gate:` | `gate` | A design-flow run parked at a gate | `POST /v1/runs/{id}/approval` |
| `tool:` | `tool_call`, `human_authority`, `flow_proposal`, `flow_version`, `flow_patch` (a patch to a running flow, showing what re-runs and what is kept) | The tool-approval ledger | `POST /v1/chat/tool_approvals/{id}` |
| `change:` | `design_change` | Assistant design-change proposals | `POST /v1/assistant/proposals/{id}/decide` |
| `design_loop:` | `design_loop` | The winning candidate of a closed design loop | `POST /v1/design-loop/{id}/approve` |
| `sketch:` | `sketch` | A design sketch work product | `POST /v1/twin/nodes/{id}/approve-sketch` |
| `drawing:` | `drawing` | A technical drawing work product | `POST /v1/twin/nodes/{id}/approve-technical-drawing` |
| `parametric_sketch:` | `parametric_sketch` | A sketch promoted from its interpretation on the Create stage, with only the accepted rules. A human-authority kind. See [the sketch compiler](twin_schema.md#sketch-compiler) | `POST /v1/twin/nodes/{id}/approve-parametric-sketch` |
| `rerun:` | `rerun` | A re-check a cross-domain change proposed (a firmware build, a DRC, an FEA) that waits for a person. A human-authority kind. Id `rerun:<impact id>:<rerun id>`. See [re-checks after a cross-domain change](#re-checks-after-a-cross-domain-change) | `/v1/approvals` only |
| `upload:` | `upload` | Files staged from outside: a plugin tool call, a signed upload link, the local plugin, or the API. A human-authority kind. See [Files in and out](file-transfer.md) | approve imports the files, reject deletes them |

## The item

`id`, `kind`, `status` (`pending`, `approved`, `rejected`, `expired`,
`canceled`, `retried`, `reworked`), `title`, `summary`, `project_id`,
`created_at`, `deadline` (ISO-8601), `route` (`dashboard`, `elicitation` or
null), `requested_by`, `reason_held`, `findings`, `allowed_decisions`,
`rework_targets`, `reason_required_for`, `decidable`, `not_decidable_reason`,
`detail`, `decision` and `base_revision`.

`base_revision` is the revision of what the item asks about, where one is
known: a sketch's or drawing's content hash, a pending gate's `gate:<digest>`
(its phase, attempt and the run's open drafts), or a pending design change's
`wp:<digest>` (the content of the work products it affects). Otherwise it is
null. See [stale decisions](#stale-decisions).

### Gate findings

For a gate, `findings` is a list of `{kind, severity, message}` with `kind` one
of `missing_deliverable`, `ungrounded`, `constraint_violation`, `analysis`,
`geometry`, `other`. They come from the live workflow state (`gate_findings`)
when the workflow can be queried, and otherwise are parsed out of the approval
reason. The classification is by wording, so an unrecognised finding is `other`
and keeps its full message.

### Allowed decisions

`allowed_decisions` is computed per item: `approve` is absent while a gate is
not ready, `retry` is absent once retries are used up, and `rework` is offered
only with `rework_targets` (the earlier phase ids of the run's own flow) while
rework cycles remain. Held tool calls, change proposals offer `approve` and
`reject`; design loops, sketches, parametric sketches and drawings offer
`approve` only.

When a pending item cannot be decided here, `decidable` is false and
`not_decidable_reason` says why. A held tool call is decidable here whichever
route asked it, including one whose question is also on screen in the client's
own prompt: the first decision wins, and the prompt's answer, if it comes
later, finds the hold already decided. See
[Held tool calls](#held-tool-calls).

## Deciding

| Status | When |
|--------|------|
| 404 | Unknown id. |
| 409 | Not decidable now: already decided, expired or canceled, gate not ready for approve, retries or reworks used up, or the run's workflow no longer exists (only `reject` is offered then). Also an `approve` whose drafts were based on revisions that changed since (`PATCH_CONFLICT`): nothing is committed and the gate stays open; `retry` rebases. See [the run's change set](architecture/design-flow-harness.md#a-runs-change-set-drafts-until-the-gate). |
| 422 | Decision not offered for this kind, invalid `to_phase`, or no `reason` for `reject`, `retry` or `rework`. |
| 200 | `reject` on a gate whose workflow no longer exists: the decision is saved and there is no run left to signal. |
| 503 | A gate decision was recorded but could not be delivered to the workflow; or committing a gate's drafts failed part-way (heads restored, approve again). |

### Stale decisions

A decision may carry the item's `base_revision` as the person saw it. When the
item has moved on since (a gate's run re-drafted, retried or reached another
gate; a design change's work products changed; a sketch was redrawn), the
decision is refused with **409**, nothing is recorded and the item stays
pending: approving a proposal nobody looked at is never possible. Read the
item again and decide on what it now shows. `POST /v1/runs/{id}/approval`
takes the same optional `base_revision` for a gate. A decision that sends
none, or an item that tracks none, is unaffected. The dashboard's approval
cards always send the revision they display.

### Identity and surface

The deciding human is always the authenticated principal, for every kind.
`reviewer`, `approved_by` and `approvedBy` in the body are ignored and logged
(`approval_body_identity_ignored`). On a gateway with authentication off the
approver is `local:dashboard` with `approver_verified: false`.

Clients say where the decision came from with headers:

- `X-MetaForge-Surface`: `dashboard`, `cli` or `agent`. Absent is recorded as
  `unknown`; any other value is a 422. A fourth surface, `chat`, is never sent
  as a header: it is recorded by the inline-decision route when a person
  answered a gate in the MCP client's prompt.
- `X-MetaForge-On-Behalf-Of`: the human an agent acts for. Only valid with
  `agent`.
- `X-MetaForge-Agent`: the agent's name, such as `claude-code`. Only valid with
  `agent`.

The `decision` block on the item holds `decision`, `reason`, `approver`,
`approver_verified`, `surface`, `on_behalf_of`, `agent` and `decided_at`. All of it is
persisted on the same record the existing handler writes, so it survives a
restart:

| Kind | Where it is stored |
|------|--------------------|
| `gate`, `tool` | The run's `request.decisions` log, written with the run ledger on the decision's transition. |
| `change` | `reviewer`, `reviewer_verified`, `decision_surface`, `decision_on_behalf_of`, `decision_agent` on the proposal. |
| `sketch`, `drawing`, `parametric_sketch` | `approved_by`, `approver_verified`, `approval_surface`, `approval_on_behalf_of`, `approval_agent` in the work product metadata. |
| `design_loop` | The same fields on the winning iteration node. |
| `rerun` | `decided_by`, `decided_at` and `decision_reason` on the rerun, inside its `change_impact` record. |

A decision made on an older route reports `surface: "unknown"`, because no
surface was sent. A refused decision leaves no entry behind. A gate that was
retried or reworked shows its decision only once the next gate resolves, since
the item is pending again at that point.

## Questions returned inside the result (MCP 2026-07-28)

A client on MCP revision `2026-07-28` cannot be sent a question mid-call:
that revision forbids it. Its held writes and gates are asked the other way
round. The call returns the question with
`resultType: "input_required"`; the client shows it, then repeats the call
with the answer. The approval entry, the approver record and the "dashboard
answer wins" rule are the same as for an inline prompt. An answer can be
used once. Details are in
[the MCP protocol notes](mcp_spec.md#questions-returned-inside-results).

## A client that answers for the person

A client declares elicitation at `initialize`, so the server routes its held
writes to that client's own prompt. Some clients answer the request themselves
without showing anything: Claude Code 2.1.x returns `decline` in about 20 ms.
Taken for a refusal, that left every held write on such a connection
unanswerable — never shown inline, never queued where a person could find it,
and reported to the agent as "a reviewer rejected it" when no reviewer had
seen it.

So a `decline` or `cancel` that comes back faster than anyone could have read
the prompt (one second, `mcp_core.elicitation.UNATTENDED_ANSWER_SECONDS`) is
not treated as an answer:

- the hold stays pending, decidable in this API, in the dashboard, with
  `approvals.decide` and in `forge approvals`, and the call returns `held`
  (see [Held tool calls](#held-tool-calls));
- the connection is marked as one that cannot be asked, so the rest of the
  session is not put to its prompt again.

Only `decline` and `cancel` are judged by the clock. An `accept` carries a
real answer however fast it arrives. It is a heuristic and it fails safe: a
genuine instant refusal leaves the call waiting rather than closed, and
nothing runs that a person has not approved. The fallback is logged as
`mcp_elicitation_unattended_fallback`, and the gate logs
`elicitation_unattended` with the action and how long it waited.

## Gates in the client chat

A design-flow gate can be put to the person in the MCP client's own chat
instead of the dashboard. The agent calls `flow.await_gate` with a
run id. The tool waits for the run's next gate, then sends an MCP elicitation
that the client shows to the person: the gate, what its checks found, and the
decisions it allows right now. The person answers; the agent does not, and
cannot.

- **Same rules as the dashboard.** The answer goes through the same
  `service.decide`. Approve is not offered on a gate whose checks failed,
  retry and rework keep their caps, and reject, retry and rework need a reason.
- **The approver is the session's person.** The sidecar takes the identity
  from the authenticated MCP session, never from the form, and posts it to
  `POST /v1/approvals/gate:{run_id}/inline-decision`. It is recorded as
  verified only when that request is itself authenticated, the same rule as a
  held tool call's inline answer. With auth off it is
  `local:elicitation`, unverified.
- **No answer is not a decision.** Dismissing the prompt, declining it, or
  letting it expire leaves the gate open in the dashboard and in
  `forge approvals`. A client that cannot show a prompt gets the gate back as
  `awaiting_gate` with a note to answer it there.
- **Fits the client's own call timeout.** Waiting for the gate and the
  person's answer happen inside one tool call, and Claude Code ends a call
  after 120 s. By default the tool waits up to 30 s for a gate and gives the
  person 80 s to answer, less any time spent waiting, so the call ends inside
  110 s. `wait_seconds` and `answer_seconds` override this for a
  client that allows longer calls. When nobody answers in time the gate stays
  open and the agent can call the tool again.
- **A gate decided elsewhere says who decided it**. When the gate
  is answered in the dashboard, with `forge approvals`, or through
  `POST /v1/approvals/{id}/decision` while the tool waits, it returns
  `decided_elsewhere` with the decision record: the decision, the approver, the
  surface, the agent and the person it acted for when an agent decided, the
  reason and the time. Every other result (`no_gate_yet`, `run_ended`,
  `decided`) and `run.get_status` carry the run's most recent decision as
  `last_gate_decision`, read from the run's own decision log. Before this a gate
  decided outside the prompt simply disappeared, and an agent reported it as
  closed with nobody approving it.
- **Not held at the call.** The tool's only effect is asking the person, so
  holding the call first would ask them twice for one decision. The
  design-flow worker cannot call it (`flow.*` is refused for the service
  caller).

The decision record shows `surface: "chat"`.

## Any approval in the client chat

`flow.await_gate` covers one run's next gate. Two tools cover everything else
waiting for a person, from the MCP client (the Claude Code plugin's
`/metaforge:approvals` command uses them):

- `approvals.list` returns the pending items, the same list as the Approvals
  page: gates, held tool calls, change proposals and twin approvals. Pass
  `project_id` to narrow it. It is read-only.
- `approvals.decide` puts one of them, by id, to the person in the client's own
  prompt, offering only the decisions the item allows right now. The person
  answers; the agent cannot. The answer is recorded through
  `POST /v1/approvals/{id}/inline-decision`, so the dashboard's rules apply:
  approve is not offered on a gate whose checks failed, retry and rework keep
  their caps, and reject, retry and rework need a reason. A held tool call is
  decidable here even while another client's prompt asks it; the first
  decision wins. Dismissing the prompt leaves the item pending. The decision
  record shows `surface: "chat"`.

Like `flow.await_gate`, `approvals.decide` is not held at the call (it only
asks), and the design-flow worker may not call either tool.

**Told without asking.** A person working in a client should not have to ask
whether something is waiting. When a client's tool call returns and an approval
has appeared that this MCP session has not been told about, the sidecar adds
one sentence to that result: how many are waiting, the new ones' titles, and
that the person can decide them with `/metaforge:approvals` or in the
dashboard. Each item is mentioned once per session; the approval tools
themselves and `flow.await_gate` carry no notice, and the design-flow worker
never gets one. The list is fetched at most every 15 seconds and a failure
to fetch it never affects the call. The plugin's session rules tell the agent
to pass the notice on straight away.

Both are on the `core` profile, the plugin's default connection. To make room
under its 40-tool cap, `memory.list_insights` and
`memory.retrieve_similar_experience` moved to the full profile.

**Held calls.** How a held tool call is answered from the client, and retried,
is in [Held tool calls](#held-tool-calls).

A held call that names a run (`run_id`, or a task id `<run_id>:<phase>:<n>`,
as `phase.claim` does) and no project is recorded under the run's project, so
it shows on that project's Approvals page rather than only under All
projects. The pending queue keeps polling in a background browser tab, so an
item that appears while the person is in another window is there when they
look.

## Held tool calls

A write that needs a person (an MCP caller outside a claimed run task, for
example `twin.render_concept_image`, `twin.generate_concept_mesh` or
`project.create`) is held as a **durable approval**. The approval outlives the
tool call: the call returns at once, a person decides from anywhere, and the
identical call made again runs once on the approval.

Before this, the call blocked while the question waited in the client's
prompt, and the client (Claude Code: 120 s) timed the call out first. An
approval given later was recorded and never applied, the retry opened a new
hold, and the dashboard could not decide the hold because it was "answered in
the client's own approval prompt".

### The lifecycle

1. **Held.** The sidecar records the approval on the ledger with its key: the
   tool, a canonical hash of the arguments (sorted JSON; the transport's
   `_meta` and reserved `__` arguments are left out), the caller's MCP session
   and the project (the `project_id` argument, else the session's project). It
   is `pending` with an expiry, 30 minutes by default
   (`METAFORGE_TOOL_APPROVAL_TTL_S`, at most 3600 seconds). A pending approval
   with the same key is reused, never opened twice.
2. **Asked briefly.** A client that can show a prompt is asked in it for up to
   20 seconds. Any other client is waited on for 20 seconds in case the person
   is already on the Approvals page, with progress notifications when the call
   carries a `progressToken`. An answer in that time runs or refuses the call
   at once.
3. **Returned.** Unanswered, the call returns straight away. It is a tool
   result with `isError: true` (the tool did not run) whose text tells the
   agent to tell the person, and whose `_meta.approval` carries:

   ```json
   {"held": true, "approval_id": "run_...", "expires_at": "2026-11-04T10:30:00+00:00",
    "expires_in_seconds": 1800, "next_step": "retry this exact call once it is approved",
    "code": "approval_required", "outcome": "awaiting_person", "route": "elicitation",
    "where": "...", "repeated": false}
   ```

   The same object is the second text block of the result, for a client that
   does not show `_meta`. The legacy `tool/call` dialect returns it as the
   JSON-RPC error data, as before.
4. **Decided anywhere.** The client's prompt, `POST /v1/approvals/{id}/decision`
   (the dashboard's Approvals page), and the plugin's `approvals.decide` all
   decide the same record. The first decision wins; a later one is a 409. A
   prompt answered after the call returned still lands on the record: the
   sidecar keeps where a withdrawn question's answer goes, and an Accept or
   Decline that arrives late approves or rejects the approval. Dismissing the
   prompt decides nothing.
5. **Retried.** The same session calling the same tool with the same arguments
   (same hash, same project) finds the decision. Approved, unexpired and
   unused: the call runs immediately and the approval is marked used. It is
   single use; the next identical call is a new hold. Rejected: the refusal is
   reported once. Pending: the same approval comes back with
   `repeated: true`, no second hold. A changed argument is a different key and
   needs a new approval.
6. **Expired.** A pending approval past its expiry is closed `timed_out`, and
   an approved one not used before its expiry is no longer honoured. The next
   identical call opens a new hold.

Durable holds survive a gateway restart: a pending one is restored as pending
until its expiry, where a blocking hold whose waiter died with the process is
marked failed as before. The approver is restored from the decision log.

**The agent is told.** When a held call of the current session is approved,
the next tool reply to that session carries one sentence: "approval `<id>` for
`<tool>` was approved; call it again with the same arguments." It is said
once, beside the pending-approvals notice
([Any approval in the client chat](#any-approval-in-the-client-chat)).

**Accept means approve.** The client prompt has no yes/no box any more: Accept
approves, Decline refuses, and the only field is an optional note. The old
form carried a required `approve` checkbox, and pressing Accept without ticking
it was recorded as a rejection. Whatever an Accept carries, it is an approval;
an Accept is never turned into a rejection.

**Who decides is unchanged.** Human-authority tools still need a person: an
agent caller of `/v1/approvals` is refused them (403), and the approver
recorded is the one the deciding surface established, never anything the
caller sent. `approver_verified` is `true` only for an authenticated
principal. The one exception is the owner's delegation, where an agent that
names the person decides for them, on the record: see
[delegated holds through MCP](#delegated-holds-through-mcp).

### A project's policy decides held writes

The [approval policy](#approval-policy-and-the-gate-reviewer) that decides a
project's gates also decides its held tool writes. Before holding a call the
sidecar asks `POST /v1/chat/tool_approvals/policy`, and the gateway applies the
policy of the project the call lands in. It asks on every hold path: a durable
hold, and the multi-round request flow that clients such as Claude Code use,
where the question is answered inside the call:

| Mode in force | Held tool writes |
|---------------|------------------|
| `off` | A person decides. |
| `advise` | A person decides. There is no reviewer for a single tool call, so nothing more is recorded. |
| `auto_clean` | Additive and illustrative writes are approved: a concept sketch, image or mesh, a render, a new record beside existing ones (the tools marked additive in the MCP annotations). Anything else is held. |
| `auto` | Any write the guardrails hold is approved, until the policy's expiry, after which it is `advise`. |

In every mode:

- `METAFORGE_AUTO_APPROVE_MAX` is the ceiling: a project at `auto` under a cap
  of `auto_clean` is decided as `auto_clean`.
- Only the call's own project counts. A call whose project is another
  project, or that has none, is decided by a person. The sidecar resolves the
  call's project before any hold, policy, delegation or approval-key decision,
  in this order: an explicit `project_id` argument; then the project the
  `metaforge_session` handle is bound to (the handle `session.start` or
  `project.open` returned); then the transport session's own scope
  (`Mcp-Session-Id` or `X-MetaForge-Session`). The policy request it sends
  (`POST /v1/chat/tool_approvals/policy`) carries that `project` and the
  `session_id`. So a tool with no `project_id` argument, such as
  `cadquery.execute_script`, called with a handle bound to a project at
  `auto`, is decided by that project's policy, and a handle for another
  project is decided by that other project's policy. With no
  argument, no handle and no session scope it names no project and is held.
- Never approved by a policy: human-authority tools, destructive tools, and
  project, flow, run, approval, review, phase, session, library and vendor
  tools. `phase.claim` keeps its own narrower rule, below. See
  [CadQuery writes under a policy](#cadquery-writes-under-a-policy) for which
  CadQuery tools are destructive and why, and how `cadquery.execute_script` is
  isolated.
- **Spend cap.** Paid tools are capped per project per rolling hour: 20
  `twin.render_concept_image` and 20 `twin.generate_sketch` (each
  `METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR`, counted per tool) and 3
  `twin.generate_concept_mesh` (`METAFORGE_AUTO_APPROVE_MESH_PER_HOUR`), and 3
  `twin.design_cad_loop` runs (`METAFORGE_AUTO_APPROVE_CAD_LOOP_PER_HOUR`), each of
  which also stops at its own model-call and spend budget. `cadquery.execute_script`
  is not paid but has an hourly cap all the same: 60 per project
  (`METAFORGE_AUTO_APPROVE_SCRIPT_PER_HOUR`), and the hold calls it an hourly cap. Over
  the cap the call is held for a person, and the hold's reason and the held
  result's `note` say the cap was reached. A person generating on the Create
  stage is the approver of their own request, but past the same hourly count
  of images the gateway answers `409 spend_confirmation_required` and
  generates only when the person confirms.
- **Audit.** Every policy approval is written to the ledger on route `policy`,
  `approved_by` `policy:<mode>:<project>` (unverified), with the tool and the
  arguments hash, and marked used at once (it runs that call, not a retry). It
  is listed on the Approvals page as approved.

#### CadQuery writes under a policy

A policy may approve a CadQuery tool only when the tool cannot change a file
outside the adapter's work folder (`CADQUERY_WORK_DIR`, `/workspace` in the
container). The classification lives in `mcp_core/annotations.py`:

| Tool | Class | `auto` approves it | Why |
|------|-------|--------------------|-----|
| `cadquery.create_parametric`, `create_assembly`, `generate_enclosure`, `generate_ros2_launch` | producing | yes | Each writes a new result file. |
| `cadquery.boolean_operation` | producing | yes | It writes only its result. A named `output_path` must resolve inside the work folder, through any `..` or symlink, and must not exist: an existing file is never overwritten. Unnamed, the result goes to a new folder of its own, `output/cadquery/boolean/<id>/`. The two input files are only read. |
| `cadquery.execute_script` | producing | yes, up to 60 per project per hour | The script runs in a child process the OS isolates (below). Its one write is the result, which the adapter places inside the work folder exactly as `boolean_operation` does: a named `output_path` must not exist, and unnamed it goes to `output/cadquery/script/<id>/`. |
| `cadquery.export_*` | destructive | no, a person decides | Each writes to a caller-supplied path and overwrites what is there. |

##### How a CadQuery script is isolated

The adapter never runs agent code in its own process. For every
`cadquery.execute_script` call it starts a fresh Python process
(`tool_registry/tools/cadquery/script_child.py`, run with `python -I -B`) and
supervises it from `script_runner.py`. The child gets only the script, its
limits and a clean environment (no adapter variables, endpoints or keys). No
Docker socket and no Docker-in-Docker are involved.

What the operating system enforces on the child, and how each was verified
against the hardened container (the `cadquery-isolation` CI job runs these
checks on every change):

| Layer | What it does | Verified by |
|-------|--------------|-------------|
| Its own uid | Started through `setpriv` as one of `CADQUERY_RUNNER_UIDS` (61000 to 61003, one per concurrent run), with no supplementary groups and every capability dropped (`--inh-caps=-all --ambient-caps=-all --no-new-privs`). It owns nothing outside its folder and cannot signal the adapter (uid 1000) or another run. | A script reports its uid as 61000 to 61003; `kill(1)` fails with `Operation not permitted`; `/proc/1/environ` is unreadable. |
| Landlock | Read-only access to the system and Python paths (`/usr`, `/etc`, `/app`, the interpreter, its own `/proc/self`); nothing else is readable, so not `/workspace`. Writable: only its private folder. Running any program (`execve`) is refused. On kernel ABI 4 or later every TCP bind and connect is refused too. | Writing `/workspace/...` gives `Permission denied`; listing `/workspace` gives `Permission denied`; the `exporters` the namespace hands the script cannot write there either. |
| seccomp (all threads) | `socket()` for anything but a Unix socket, `fork`, `vfork`, `clone` of a new process, `clone3`, `execve`, `ptrace`, `process_vm_*`, `io_uring_*`, namespace, mount, keyring, BPF and `perf_event_open` calls are refused. | A TCP connect to the adapter's own listening port gives `Permission denied`; a fork bomb fails at the first `fork` with `Operation not permitted`; `subprocess` cannot start anything. |
| Resource limits | Memory 2 GB (`CADQUERY_RUNNER_MEMORY_MB`) on `RLIMIT_DATA`, which counts every private writable mapping (heap, anonymous `mmap`, thread stacks) but not address space that is only reserved; address space 16 GB (`CADQUERY_RUNNER_ADDRESS_SPACE_MB`) only as a backstop; file size 64 MB (`CADQUERY_RUNNER_MAX_FILE_MB`), 256 open files, 256 processes and threads for its uid, CPU time twice the deadline, no core dumps, and `oom_score_adj` 1000 so memory pressure kills the child, not the adapter. The child's environment caps glibc malloc arenas at two (`MALLOC_ARENA_MAX`) and BLAS and OpenMP at one thread. If the kernel's `RLIMIT_DATA` does not count `mmap` (before Linux 4.7, or with `ignore_rlimit_data`), the child finds out with an untouched probe mapping and puts the 2 GB on address space instead. | An 8 GB allocation stops with the memory limit; a filleted, shelled and lofted part on a 16-thread OCCT pool exports to STEP; endless printing or writing stops with the file size limit; 2,000 threads stop at `can't start new thread`. |
| Wall clock | The adapter kills every process of the run's uid at the script's `timeout` plus a start-up allowance (`CADQUERY_RUNNER_STARTUP_GRACE_S`, 15 s), then removes the private folder. | An infinite loop and a long sleep are killed; afterwards no process of a runner uid is left and the runner folder is empty. |
| Private folder | `/run/cadquery-runner/run-<random>` on a tmpfs, mode 0700, created by the child itself. The folder above it is mode 1733: a runner uid may create its folder but not list the others. | |

**Why memory is bounded on `RLIMIT_DATA`, not address space.**
The runner first capped address space (`RLIMIT_AS`) at 2 GB, and 28 of 50
ordinary desk-lamp parts failed with the memory limit on a 16-CPU host. OCCT
starts one worker thread per CPU and glibc gives every thread that allocates
an arena of its own, each reserving 64 MB of address space it mostly never
touches. Measured in the adapter image, for a 130 x 90 x 47 mm base filleted,
shelled and drilled with 77 holes, unioned with a loft and a finned head,
then exported to STEP and STL:

| OCCT threads | Child environment | Peak address space (VmPeak) | Peak resident (VmHWM) | Peak private writable (VmData) |
|---|---|---|---|---|
| 6 | before | 1.58 GB | 0.63 GB | 0.42 GB |
| 16 | before | 2.34 GB (fails under a 2 GB `RLIMIT_AS`) | 0.67 GB | 0.52 GB |
| 32 | before | 3.49 GB | 0.69 GB | 0.72 GB |
| 16 | arenas capped at 2, one BLAS thread | 1.38 GB | 0.66 GB | 0.42 GB |
| 32 | arenas capped at 2, one BLAS thread | 1.51 GB | 0.66 GB | 0.58 GB |

Resident memory barely moves with the thread count; address space grows by
about 70 MB a thread. A per-child cgroup (`memory.max`) would bound resident
memory exactly, but it is not available here: Docker mounts
`/sys/fs/cgroup` read-only in the container, and creating a child cgroup
needs a writable, delegated subtree that an unprivileged adapter (uid 1000,
only `SETUID` and `SETGID`) does not have. `RLIMIT_DATA` is the closest bound
a process can set on itself. A script that deliberately maps shared memory,
which `RLIMIT_DATA` does not count, is still stopped by the 16 GB address
space backstop and, before that, by the container's memory limit, under which
the kernel kills the child first.

The container's memory limit (6G) is sized for the runner: two children at
once (`CADQUERY_RUNNER_MAX_CONCURRENT`, matching its two CPUs) at 2 GB each,
the adapter with CadQuery loaded (about 0.7 GB) and its two 512 MB tmpfs
mounts, which count as memory. Raising either runner variable means raising
that limit too.

The result comes back only as bytes on the child's stdout pipe, capped at
64 MB (`CADQUERY_RUNNER_MAX_OUTPUT_MB`); the adapter never opens a path the
child names. It decodes the bytes, checks a STEP starts with `ISO-10303-21`,
accepts only the numeric volume, area and bounding box, and writes the file
itself. The response still carries `step_base64` for `twin.commit_geometry`.

The container around the adapter is hardened in `docker-compose.yml`:
`read_only: true` (tmpfs for `/tmp` and `/run/cadquery-runner`),
`cap_drop: [ALL]` with only `SETUID` and `SETGID` added back,
`no-new-privileges:true` and a limit of 512 processes (`deploy.resources.limits.pids`). The image starts as root only
so `cadquery-start` can drop to uid 1000 keeping those two capabilities as
ambient capabilities, which is what lets the adapter start a child as another
uid. `/workspace` stays writable for the adapter itself, because its other
tools (`create_*`, `boolean_operation`, `export_*`) write their results there;
no script child can reach it.

`CADQUERY_RUNNER_REQUIRE` (`uid,landlock,seccomp` in the image and the compose
file) lists the layers that must hold. If one cannot be applied, for example a
host kernel without Landlock or a container started without `SETUID` and
`SETGID`, the run is refused with the reason and nothing executes; it never
falls back to running unconfined. `CADQUERY_SCRIPT_ISOLATION=in_process` runs
scripts in the adapter's own process instead, for development and unit tests
only: that mode is not isolated, and the producing classification assumes it
is never set on a deployment.

What is not isolated:

- The child shares the container's network namespace. Network access is cut
  by seccomp (no non-Unix socket) and Landlock (no TCP), not by an empty
  namespace: creating a namespace needs `CAP_SYS_ADMIN` under Docker's default
  seccomp profile, which the container does not get. A Unix socket can still be
  created; no service in the container listens on one.
- The child can read what any process may read on the paths above, such as
  the adapter's code under `/app` and the files in `/etc`. None of it is
  secret; secrets reach the adapter only through its environment, which the
  child does not get.
- The source checks (line limit, blocked names, restricted builtins) still run
  first, but they are defence in depth. A script that reaches `os` through
  dunder attributes is stopped by the layers above, not by them.

## Re-checks after a cross-domain change

When a design item's head moves, the twin walks its interface edges and
records what the change affects (see
[interface edges and cross-domain impact](twin_schema.md#interface-edges-and-cross-domain-impact)).
Each result the change made stale, and each directly affected item, gets a
proposed re-check: `kicad.run_drc` or `kicad.run_erc` on a board,
`firmware.build` or `firmware.static_check` on firmware, `calculix.run_fea` on
the geometry a simulation used. A re-check is proposed, never started by the
walk. Its disposition comes from the rules that already decide held writes:

| Re-check | Disposition |
|----------|-------------|
| Consequential: a firmware build or test, any write to a firmware or PCB item, or a target in a baseline (release-bound) | `requires_approval`, always, whatever the policy (XDM-007) |
| A tool no policy may approve (`never_auto_approved`) | `requires_approval` |
| A read-only check (`kicad.run_drc`, `kicad.run_erc`, `firmware.static_check`): the guardrails never hold it | `may_run_unattended`, status `queued` |
| A producing analysis (`calculix.run_fea`) | `may_run_unattended` only when the project's policy approves that tool (`policy_may_approve`, so `auto`), else `requires_approval` |

A re-check that requires approval is a `rerun` item here, a human-authority
kind: an agent cannot decide it unless the gateway owner delegates human
authority. Approve or reject it like any other item; nothing else holds. Its
`base_revision` is the change it is about (`KEY@n`). Approving records the
decision on the impact; the agent or person then runs the tool, and its new
result supersedes the stale one through its revision pins. When no re-check of
a change is left waiting, the change's operation completes. A queued
(unattended) re-check is not started by MetaForge either: it is listed so the
agent or person may run it without asking.

## Approval policy and the gate reviewer

Each project has an **approval policy** that says who decides its design-flow
gates. A **reviewer agent** reads each gate and recommends a decision; the
policy says what that recommendation does.

| Mode | At each gate | Who decides |
|---|---|---|
| `off` (default) | Nothing extra. | A person |
| `advise` | The reviewer's recommendation, reasons, concerns and confidence are shown with the gate. | A person |
| `auto_clean` | When the gate's checks all passed with no findings and the reviewer recommends approve with confidence 0.8 or more, the reviewer's approval is recorded. Anything else waits. | The reviewer for clean gates, a person otherwise |
| `auto` | The reviewer decides approve, retry or rework (never reject) with confidence 0.6 or more (a rework of the phase under review is recorded as the retry it is), until the policy's expiry (24 hours by default, a week at most), then `advise` again. | The reviewer until it expires |

In every mode a gate whose checks failed never offers approve, a `reject`
recommendation is never acted on, and the reviewer's decisions are recorded
with the approver `reviewer:<source>:<model>` and surface `reviewer`, never as
a person.

**Setting it.** `GET /v1/projects/{id}/approval-policy` returns the stored
policy, the mode in force now and the operator cap.
`PUT /v1/projects/{id}/approval-policy` with `{mode, reviewer, reviewer_model,
expires_in_hours}` sets it. Changing the policy is a person's decision: a
request with `X-MetaForge-Surface: agent` gets 403, and no MCP tool sets it.
The gateway's `METAFORGE_AUTO_APPROVE_MAX` (default `advise`) caps the highest
mode any project may use; a mode above it is refused with 422.

**Where the reviewer runs.** After a gate is announced, the design-flow
worker runs one `review_gate` step (the in-process engine does the same). It
reads the policy and, unless it is `off`, builds a **review brief** (the
phase's objective and deliverables, the twin's work products for the project,
the gate's check results and the run's earlier decisions), runs a review turn
with its own model, the `gate-review` skill as its rubric and the read-only
`review` tool profile, and posts the result to `POST /v1/runs/{id}/gate-review`.
The gateway stores it on the gate (`review` on the approval item) and applies
the policy.

**The reviewer's model.** The server reviewer runs on the policy's
`reviewer_model` when it names one (`provider:model`, or a model id for the
gateway's provider), else on `METAFORGE_REVIEWER_MODEL` (same format, set on
the gateway and the design-flow worker), else on the gateway's own chat model
(a `gate_check` route when one is configured, otherwise `METAFORGE_LLM_MODEL`
or the durable selection). Every review names the model that actually
reviewed: `model` is the `provider:model` the provider pipeline recorded for
the reply, so a review on the default model no longer reads as `default`, and
`model_source` says which setting chose it (`policy`, `env` or `default`).
The review step logs `gate_review_model_selected` and `gate_review_posted`
with both, and the gateway's `gate_review_recorded` log carries them too. A
weak default model can hold or retry a stronger agent's work, so set one of
the two on any deployment whose chat model is not the one that should judge.
A client reviewer is asked for the policy's `reviewer_model` only; the env
setting names a model for the gateway's own provider chain. A reviewer
that fails posts `recommendation: none` with the error, and the gate waits for
a person as it would have anyway. `reviewer: client` leaves the review to a
connected client's reviewer subagent.

**A gate already waiting is reviewed too.** The review step runs when a gate
opens, so a gate that opened while the policy was `off` (or whose review
failed without being posted) would otherwise never get one. Two things now
ask for it later, once per gate (phase and attempt): setting a project's policy
to anything but `off` reviews every open, unreviewed gate in that project, and
the run supervisor asks for the review of a gate still unreviewed three
minutes after it opened (leaving the worker's own review time to land) while
the policy is not `off`. The review is then applied by the policy as
usual; the supervisor's log records it as a `review` entry.

**A client as the reviewer.** With `reviewer: client` (or
`client_then_server`) the review step opens a **review task** with the brief
and the model the policy asks for, and waits up to
`METAFORGE_CLIENT_REVIEW_WAIT_SECONDS` (default 600). A connected client's
reviewer takes it with `review.list_tasks`, `review.claim` and `review.submit`
(routes `/v1/review-tasks`); the Claude Code plugin ships the
`metaforge-gate-reviewer` subagent for this, running on its own model (Opus by
default, set in the subagent's `model` line). The review is recorded on the
gate as `reviewer:client:<model>`. When nobody takes the task in time,
`client_then_server` runs the server reviewer and `client` records that no
review came. The server cannot tell a subagent from the session that ran it,
so a review submitted by the same client that did a client-mode phase is
marked `same_client` and is advice only: the policy never acts on it. The
design-flow worker can call none of the `review.*` tools.

**In the dashboard.** A project's page has an **Approval policy** panel: the
mode in force, who set it, the reviewer and its model, and for `auto` how long
it lasts. Modes above the gateway's cap are shown but cannot be chosen.

**Evidence before trust.** When a person decides a gate that has a review,
`metaforge_gate_review_agreement_total` records whether they agreed. The
`GateReviewerDisagreesOften` alert fires when people overrule it on more than
30% of at least 20 gates in a week, and `GateReviewerFailing` when most
reviews come back with no recommendation. Run a project in `advise` until the
agreement is high before moving it to `auto_clean`.

**A phase claim under `auto`.** In a client-mode run each phase starts with
`phase.claim`, and an unverified client's claim is held for a person. When the
project's policy is in effect `auto`, a `phase.claim` on an open (or already
claimed) task of the project's own client-mode run, while that run is still going, is approved by
the policy instead: the MCP server asks
`POST /v1/chat/tool_approvals/policy` before holding the call, and the gateway
records the approval on the ledger like any other, on route `policy`,
approved by `policy:auto:<who set the policy>` (unverified). A claim lets one
session do one phase of one run, and its work still goes through the gate the
reviewer decides, so it adds nothing the person who chose `auto` had not
already handed over. A claim on a task that was submitted or belongs to
another project, and a claim under any other mode, are held for a person as
before. No agent approves anything here and no model judges it. Other held
writes are decided by
[the policy rules for held tool calls](#a-projects-policy-decides-held-writes).

## Delegating approvals to an agent

An agent such as Claude Code can run `forge approvals` for you. The decision is
recorded as yours, taken by the agent, never as an anonymous dashboard click.

**Permission rule.** In `.claude/settings.local.json`, allow the command group
and tell the CLI who the agent is and whom it acts for:

```json
{
  "permissions": { "allow": ["Bash(forge approvals:*)"] },
  "env": {
    "METAFORGE_APPROVAL_AGENT": "claude-code",
    "METAFORGE_APPROVAL_ON_BEHALF_OF": "you@example.com",
    "METAFORGE_AUTH_TOKEN": "<your token, if the gateway needs one>"
  }
}
```

With both `METAFORGE_APPROVAL_AGENT` and `METAFORGE_APPROVAL_ON_BEHALF_OF` set,
both CLIs send `X-MetaForge-Surface: agent`, `X-MetaForge-Agent` and
`X-MetaForge-On-Behalf-Of`.

**What is recorded.**

| Gateway | `approver` | `approver_verified` | `surface` | `agent` | `on_behalf_of` |
|---------|------------|---------------------|-----------|---------|----------------|
| Auth on, token valid | the authenticated principal | `true` | `agent` | the agent name | the user; must be that principal, else 403 |
| Auth off (local dev) | the `on_behalf_of` user | `false` | `agent` | the agent name | the user |

An agent decision without `X-MetaForge-On-Behalf-Of` is a 422.

**Human-authority approvals stay human.** Items of kind `human_authority`
(`twin.attempt_promotion`, `twin.approve_design_loop`,
`twin.approve_engineering_change`), `design_loop`, `upload`, `parametric_sketch` and
`rerun` cannot be decided by an agent. For an agent caller they show `decidable: false` with an explanation,
and a decision attempt is a 403. The gateway owner can delegate them by setting
`METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY=true` on the gateway (default off).
The compose stack passes it from `.env` to both the gateway and the MCP
sidecar, which decide the calls that land on them. Over MCP the same flag lets
an agent decide an upload it staged itself and register or update library
entries, see [an agent's own uploads and library entries](#an-agents-own-uploads-and-library-entries).

The same flag delegates the Imagine handover, rework and acceptance. With
it on, `POST .../imagine/handover` and `.../imagine/rework` accept the agent
surface with `X-MetaForge-Agent` and `X-MetaForge-On-Behalf-Of` (422 without
them), and the MCP tools `twin.imagine_handover`, `twin.imagine_rework` and
`twin.imagine_accept` stop refusing. Those tools are human-authority tools,
so no approval policy approves them; over MCP they are decided as described
in [delegated holds through MCP](#delegated-holds-through-mcp). The studio's
record and the operations log name the agent and the person it acted for,
never the person alone. Set the flag where the call lands: the gateway, and
the MCP sidecar when it serves the twin tools. See
[the Imagine stage](imagine-stage.md#delegated-to-an-agent).

### Delegated holds through MCP

A headless agent cannot answer a prompt. Before this, a human-authority
call it made over MCP (`twin.imagine_handover` in a fully autonomous lamp
run) was held on the client's own approval prompt, nobody answered, and the
run stopped there. Now, when all of these hold, the call is not held:

- the tool is human-authority (`twin.attempt_promotion`,
  `twin.approve_design_loop`, `twin.approve_engineering_change`,
  `twin.confirm_sketch_reading`, `twin.imagine_handover`,
  `twin.imagine_rework`, `twin.imagine_accept`), or one that records the
  approving person (`twin.state_manufacturing_context`, see
  [stating the manufacturing context](#stating-the-manufacturing-context));
- `METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY` is true in the MCP server that
  decides the hold, and in the gateway that records it (the gateway refuses
  with 403 otherwise, and the call is held);
- the MCP HTTP request names the agent in `X-MetaForge-Agent` and the person
  in `X-MetaForge-On-Behalf-Of` (a headless Claude Code agent puts both in its
  `.mcp.json` headers, beside `Authorization`);
- when the session has a verified principal, the person named is that
  principal.

The server then records a **delegated approval** on the ledger
(`POST /v1/chat/tool_approvals/delegated`) and runs the call once:

| Field | Value |
|-------|-------|
| `route` | `delegated` (on the held call's request) |
| `approver` | the verified principal, else the person named |
| `approver_verified` | `true` only for a verified principal on an authenticated request; `false` with auth off |
| `surface` | `agent` |
| `agent`, `on_behalf_of` | the two header values |
| `reason` | "decided by the agent for the person it acts for: the gateway owner delegates human-authority decisions" |

It is the same decision record `forge approvals` writes for an agent
decision, so the Approvals page shows it as approved "through an agent
(claude-code-e2e for you@example.com)", and the tool records the person as the
approver it acted for, never a name the model wrote. The approval is used by
that call and is never a pass for a retry.

Everything else is unchanged. With the flag off, or without
`X-MetaForge-On-Behalf-Of` (or the agent name), the call is held as before.
A destructive tool that is not human-authority keeps its own rules: a person,
or the project's approval policy. This is delegation by the owner, not
policy: no approval policy ever approves a human-authority tool, in any mode.

### Stating the manufacturing context

`twin.state_manufacturing_context` is not human-authority: without a person an
agent's values are assumptions, and the fields that are never assumed (the
machine or supplier, target cost, lead time) are refused. That stopped a
headless run at DFM: with no machine stated, no part could be assessed against
a machine's limits. Under the same delegation (the flag, both headers, the
person-mismatch guard), the call is recorded as a delegated approval like the
tools above, and the dispatcher hands the tool the agent as well as the
person. The agent may then state what only the person could:

- the never-assumed fields of a proposal, and every field of a `user_stated`
  call, are recorded `user_stated` with provenance source `mcp_delegated`,
  actor `agent:<name>`, `on_behalf_of` the person and route `delegated`;
- the other fields of a proposal stay assumptions;
- a machine named in `machine_or_supplier_profile` ("Bambu Lab P1S") that the
  machine library holds is linked by `machine_id`, so DFM assesses each part
  against that machine's limits; a name the library does not hold is
  recorded as stated.

The record names the agent and the person, never the person alone, and never
an assumption. With the flag off, or without either header, the call keeps
its rules: held for a person, or approved by the project's policy, and the
never-assumed fields are refused.

### An agent's own uploads and library entries

Two more steps stopped a headless run for a person nobody could reach: the
upload the agent staged itself (an approval of kind `upload`), and a library
registration such as a standard ISO 4762 M4x35 screw, which was held on a
client prompt. `METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY` now reaches both, under
the same conditions as [delegated holds through MCP](#delegated-holds-through-mcp)
(the flag in the MCP server and the gateway, both headers, the person-mismatch
guard):

| Step | Tool | What happens | What is recorded |
|------|------|--------------|------------------|
| The agent's own upload | `files.decide_upload` (`upload_id`, `decision` approve or reject, `reason` to reject) | Decides the upload through the approvals service with surface `agent`. Refused for an upload another agent or a person staged: the upload's `agent` (the `X-MetaForge-Agent` it was staged with) must be the caller's. | The upload's `decision` carries `agent` and `on_behalf_of`, `approver_verified` is `false`, and each imported work product's metadata has `approved_by_agent`. The Approvals page shows it as decided through the agent for the person. |
| Library writes | `library.register_*`, `library.update_*` (components, materials, processes, appearances, ergonomic limits, machines) | Not held: it runs at once. | The entry's history records the change as `agent <agent> on behalf of <person>`, and every history version and tool result carries an `authority`: `{kind: delegated, agent, on_behalf_of, verified_by_person: false}`. Anything else reads `{kind: recorded, by, verified_by_person: false}`. |

Each call is first recorded on the ledger as a delegated approval (route
`delegated`, surface `agent`, the agent and the person). `files.decide_upload`
exists only for a delegated agent: with the flag off, or without both headers,
it is refused at once rather than held, because a person decides an upload
directly in the dashboard, with `forge approvals` or with `approvals.decide`.
Through `/v1/approvals`, an agent caller with the flag on may decide an upload
item as before; the decision now records the agent and the person on the
upload too.

What does not change:

- With the flag off, a library write is held for a person exactly as before.
- No approval policy approves these, in any mode, `auto` included: the
  library tools stay on the never-auto-approved list, and `files.decide_upload`
  is destructive.
- `library.dispatch_job` (a print on a real machine), `library.record_job_result`
  and `vendor.place_order` (spends money) are never delegated by any flag.
  The gateway refuses to record a delegated approval for them with 422.

### Delegating a release to an agent

The last two steps of a release are a person's: fixing the configuration
baseline (`twin.create_baseline`, which advances every member's authority to
`baselined`) and approving the manufacturing release (the `release_approval`
entity the release checklist's "Manufacturing release approved" item reads).
Neither is human-authority, so `METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY` does not
reach them, and the baseline is destructive, so no approval policy approves it.
A fully autonomous run therefore stopped at the baseline.

The owner can delegate both with a separate switch,
`METAFORGE_ALLOW_AGENT_RELEASE=true` (default off). Set it where the call
lands: the gateway, and the MCP sidecar. The compose stack passes it from
`.env` to both. The human-authority flag alone does not delegate a release,
and the release flag does not delegate human-authority steps.

With the flag on, and the MCP request naming the agent in
`X-MetaForge-Agent` and the person in `X-MetaForge-On-Behalf-Of` (the person
must be the verified principal when there is one):

| Step | Tool | What happens | What is recorded |
|------|------|--------------|------------------|
| Baseline | `twin.create_baseline` (`project_id`, `reason`) | Not held: it runs at once. | The baseline's `approved_by` is `agent:<agent> on behalf of <person>` and its `reason` names the agent, the person and the release flag. An `approved_by` argument is ignored. |
| Release approval | `twin.approve_release` (`project_id`, `reason`, optional `entity_id`) | Approves the project's newest `release_approval` that is not approved yet (record it first with `twin.record_engineering_entity`). | The entity's `authority` becomes `approved`, its metadata says `approved_by: agent:<agent>` and `on_behalf_of: <person>`, and the checklist item passes with the detail "approved by agent `<agent>` on behalf of `<person>`". |
| Accepting a failing check | `twin.accept_release_finding` (`project_id`, `item`, `targets`, `reason`) | Accepts a failing ERC or DRC (`eda_checks`) or open assembly findings (`assembly_dfa`) for the release. Only a target that currently fails can be named. | An approved `waiver` with `approved_by: agent:<agent>`, `on_behalf_of: <person>`, `approval_route: delegated` and the reason, pinned to the revisions it is about; the checklist item's detail says "accepted by agent `<agent>` on behalf of `<person>`". Without the flag it is held for a person, and the person who approves the hold is recorded. |

Each call is first recorded on the ledger as a delegated approval, the same
record as [delegated holds through MCP](#delegated-holds-through-mcp): route
`delegated`, surface `agent`, the agent and the person, and the reason
"decided by the agent for the person it acts for: the gateway owner delegates
the release baseline and approval (METAFORGE_ALLOW_AGENT_RELEASE)". It is never
recorded as the person's own decision.

The release rules stay as they are. `twin.approve_release` refuses, naming each
blocker, while any other item of the manufacturing release checklist that the
release gate reads (every component exact, DFM, verification, STEP round trip,
ERC and DRC, the assembly check, the outputs' sources) is open, and `twin.create_release_package` still needs the whole G8 gate,
baseline and approval included.

`twin.approve_release` exists only for a delegated agent. With the flag off,
or without both headers, or with a person who is not the principal, it is
refused at once (no hold), and `twin.create_baseline` is held for a person
exactly as before. The gateway refuses to record a delegated release with 403
while its own flag is off, and with 422 when the agent or the person is
missing. An agent deciding a held `twin.create_baseline` through
`/v1/approvals` or `approvals.decide` gets 403 unless the release flag is on.
No approval policy approves either tool, in any mode: this is delegation by
the owner, not policy.

A person still approves a release with `twin.approve_engineering_entity` or
in the dashboard.

## Observability

`metaforge_approval_decision_total{kind, decision, surface, outcome}` counts
decisions made through this API, with `outcome` one of `ok`, `refused` (a 4xx)
or `error`. There is no alert: a refusal is a reviewer answering something
already closed, not a fault.

`metaforge_tool_approval_total{outcome}` counts durable tool approvals:
`held`, `approved` (answered while the call waited), `approved_late_honoured`
(an identical retry ran on it), `rejected`, `auto_approved`, `cap_held`,
`delegated` (an agent decided a human-authority call for a person),
`delegated_release` (an agent fixed the baseline or approved the release for
a person) and `expired`. The gateway logs the same as `tool_approval_held`,
`tool_approval_approved_late_honoured`, `tool_approval_auto_approved`,
`tool_approval_cap_held`, `tool_approval_delegated`,
`tool_approval_delegated_release` (and `tool_approval_release_not_delegated`
when its release flag is off) and `tool_approval_expired`; the sidecar logs
`mcp_tool_call_held_for_approval` (with `durable=true` and the arguments
hash), `mcp_tool_call_handed_off`, `mcp_tool_approval_approved_late_honoured`,
`mcp_tool_call_delegated`, `mcp_release_delegated`, `mcp_release_not_delegated`
(a refused `twin.approve_release`), and `mcp_delegation_not_claimed` or
`mcp_delegation_person_mismatch` when a delegation was not taken. The tools log
`twin_baseline_delegated`, `release_approval_delegated` and
`release_approval_refused_blocked`, and count `twin.approve_release` calls in
`metaforge_release_path_call_total{tool, outcome}`.
The `ToolApprovalsMostlyExpiring` alert fires when more than half of the holds
in six hours expire undecided.

See [Gateway API reference](reference/gateway-api.md) for the generated schema.
