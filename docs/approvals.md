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
| `upload:` | `upload` | Files staged from outside: a plugin tool call, a signed upload link, the local plugin, or the API. A human-authority kind. See [Files in and out](file-transfer.md) | approve imports the files, reject deletes them |

## The item

`id`, `kind`, `status` (`pending`, `approved`, `rejected`, `expired`,
`canceled`, `retried`, `reworked`), `title`, `summary`, `project_id`,
`created_at`, `deadline` (ISO-8601), `route` (`dashboard`, `elicitation` or
null), `requested_by`, `reason_held`, `findings`, `allowed_decisions`,
`rework_targets`, `reason_required_for`, `decidable`, `not_decidable_reason`,
`detail` and `decision`.

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
`reject`; design loops, sketches and drawings offer `approve` only.

When a pending item cannot be decided here, `decidable` is false and
`not_decidable_reason` says why. A hold on the `elicitation` route is the main
case: the question is on screen in the client's own prompt, and that prompt
gets the answer. Unless it was never shown — see
[A client that answers for the person](#a-client-that-answers-for-the-person),
where the elicitation hold is closed and the call is held again on the
`dashboard` route, which is decidable here.

## Deciding

| Status | When |
|--------|------|
| 404 | Unknown id. |
| 409 | Not decidable now: already decided, expired or canceled, elicitation route, gate not ready for approve, retries or reworks used up, or the run's workflow no longer exists (only `reject` is offered then). Also an `approve` whose drafts were based on revisions that changed since (`PATCH_CONFLICT`): nothing is committed and the gate stays open; `retry` rebases. See [the run's change set](architecture/design-flow-harness.md#a-runs-change-set-drafts-until-the-gate). |
| 422 | Decision not offered for this kind, invalid `to_phase`, or no `reason` for `reject`, `retry` or `rework`. |
| 200 | `reject` on a gate whose workflow no longer exists: the decision is saved and there is no run left to signal. |
| 503 | A gate decision was recorded but could not be delivered to the workflow; or committing a gate's drafts failed part-way (heads restored, approve again). |

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
| `sketch`, `drawing` | `approved_by`, `approver_verified`, `approval_surface`, `approval_on_behalf_of`, `approval_agent` in the work product metadata. |
| `design_loop` | The same fields on the winning iteration node. |

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

- the elicitation hold is closed as `cancelled`, so nothing is left in the
  queue looking as though somebody had been asked;
- the same call is held again on the `dashboard` route, where it is pending
  and decidable in this API, in the dashboard and in `forge approvals`;
- the connection is marked as one that cannot be asked, so the rest of the
  session queues straight away instead of burning a hold per call;
- with no dashboard gate configured the call is refused as
  `not_configured`. The fallback is a queue somebody can see, not a way
  through the gate.

Only `decline` and `cancel` are judged by the clock. An `accept` carries a
real answer however fast it arrives, so a person who clicks No quickly is
still a rejection. It is a heuristic and it fails safe: a genuine instant
refusal leaves the call waiting in the dashboard rather than closed, and
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
  their caps, reject, retry and rework need a reason, and a hold already being
  asked in another client's prompt is not decidable here. Dismissing the prompt
  leaves the item pending. The decision record shows `surface: "chat"`.

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

**A held call is handed to the person.** Some clients can neither show an
approval prompt nor be sent progress notifications, the claude.ai connector
among them. Holding a write open for one of them was a silent wait: the person
saw a tool call that would not finish, never an approval, and the hold ran out.
For such a client the sidecar instead:

1. records the hold on the ledger with a 30-minute window and waits 20 seconds
   in case the person answers at once;
2. returns a JSON-RPC error with `code: "approval_required"`,
   `outcome: "awaiting_person"`, the `approval_id`, `where` (the dashboard
   Approvals page and its link), `expires_in_seconds`,
   `next: "call_again_with_same_arguments_after_approval"` and
   `retryable: true`. Its message tells the agent to tell the person, and to
   call the tool again with the same arguments once the person has approved;
3. on that repeated call (same MCP session, tool and arguments) reads the
   decision. Approved: the call runs, once, with the approver taken from the
   ledger. Still pending: the same approval comes back with no second hold.
   Rejected or expired: the usual refusal.

The person decides on the dashboard. The agent's own word is never taken as
the decision, so a model cannot approve its own write by saying the person
agreed. A client that can show a prompt is still asked inline, and one that
receives progress still waits with its window.

A held call that names a run (`run_id`, or a task id `<run_id>:<phase>:<n>`,
as `phase.claim` does) and no project is recorded under the run's project, so
it shows on that project's Approvals page rather than only under All
projects. The pending queue keeps polling in a background browser tab, so an
item that appears while the person is in another window is there when they
look.

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
already handed over. Every other write, a claim on a task that was submitted or
belongs to another project, and every other mode are held for a person as
before. No agent approves anything here and no model judges it.

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
`twin.approve_engineering_change`), `design_loop` and `upload` cannot be decided by an
agent. For an agent caller they show `decidable: false` with an explanation,
and a decision attempt is a 403. The gateway owner can delegate them by setting
`METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY=true` on the gateway (default off).

## Observability

`metaforge_approval_decision_total{kind, decision, surface, outcome}` counts
decisions made through this API, with `outcome` one of `ok`, `refused` (a 4xx)
or `error`. There is no alert: a refusal is a reviewer answering something
already closed, not a fault.

See [Gateway API reference](reference/gateway-api.md) for the generated schema.
