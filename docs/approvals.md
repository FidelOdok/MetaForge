# Unified approvals API

Every human approval MetaForge asks for is reachable through one API,
`/v1/approvals` (FORGE-506, FORGE-507). A dashboard queue, the CLI and an agent
all list, inspect and decide through the same three routes and read the same
normalized item, whatever the approval is about.

The older per-kind routes still work and are unchanged for callers. Both
surfaces call the same decision functions, so the 404, 409 and 422 rules are
one body of code.

## Routes

| Route | Purpose |
|-------|---------|
| `GET /v1/approvals?status=pending\|decided\|all&project_id=<id>&kind=<kind>` | List items. `decided` is every item that is not pending (the audit view). |
| `GET /v1/approvals/{id}` | One item. |
| `POST /v1/approvals/{id}/decision` | Body `{"decision", "reason", "to_phase"}`. Returns the updated item. |

The list response is `{"items": [...], "unscoped_count": n}`. When scoped to a
project, `unscoped_count` is how many items were left out because they carry no
project, so a hidden item is reported rather than silently dropped.

## What is covered

The id is `<prefix>:<native id>`. The prefix says which store holds the item;
`kind` is finer.

| Id prefix | `kind` | Source | Decided by |
|-----------|--------|--------|------------|
| `gate:` | `gate` | A design-flow run parked at a gate | `POST /v1/runs/{id}/approval` |
| `tool:` | `tool_call`, `human_authority`, `flow_proposal`, `flow_version` | The tool-approval ledger | `POST /v1/chat/tool_approvals/{id}` |
| `change:` | `design_change` | Assistant design-change proposals | `POST /v1/assistant/proposals/{id}/decide` |
| `design_loop:` | `design_loop` | The winning candidate of a closed design loop | `POST /v1/design-loop/{id}/approve` |
| `sketch:` | `sketch` | A design sketch work product | `POST /v1/twin/nodes/{id}/approve-sketch` |
| `drawing:` | `drawing` | A technical drawing work product | `POST /v1/twin/nodes/{id}/approve-technical-drawing` |

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
gets the answer.

## Deciding

| Status | When |
|--------|------|
| 404 | Unknown id. |
| 409 | Not decidable now: already decided, expired or canceled, elicitation route, gate not ready for approve, retries or reworks used up. |
| 422 | Decision not offered for this kind, invalid `to_phase`, or no `reason` for `reject`, `retry` or `rework`. |
| 503 | A gate decision was recorded but could not be delivered to the workflow. |

### Identity and surface

The deciding human is always the authenticated principal, for every kind.
`reviewer`, `approved_by` and `approvedBy` in the body are ignored and logged
(`approval_body_identity_ignored`). On a gateway with authentication off the
approver is `local:dashboard` with `approver_verified: false`.

Clients say where the decision came from with headers:

- `X-MetaForge-Surface`: `dashboard`, `cli` or `agent`. Absent is recorded as
  `unknown`; any other value is a 422.
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
`twin.approve_engineering_change`) and `design_loop` cannot be decided by an
agent. For an agent caller they show `decidable: false` with an explanation,
and a decision attempt is a 403. The gateway owner can delegate them by setting
`METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY=true` on the gateway (default off).

## Observability

`metaforge_approval_decision_total{kind, decision, surface, outcome}` counts
decisions made through this API, with `outcome` one of `ok`, `refused` (a 4xx)
or `error`. There is no alert: a refusal is a reviewer answering something
already closed, not a fault.

See [Gateway API reference](reference/gateway-api.md) for the generated schema.
