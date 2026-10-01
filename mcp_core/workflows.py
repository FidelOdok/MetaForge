"""The curated MetaForge workflows, in one place (FORGE-340/341).

Six things an engineer asks a harness to do. They are exposed twice, from
this one definition:

* as **MCP prompts** (``prompts/list`` / ``prompts/get``), which every
  spec-compliant client can use — ChatGPT, claude.ai, Hermes, Codex;
* as **slash commands** in the generated Claude Code package, because a
  harness with its own command surface should use it.

Defining them twice would mean the Claude Code user and the ChatGPT user
getting different instructions for the same task, and the one that drifts
being whichever nobody is currently testing.

The wording matters as much as the steps. Each of these says what the server
will actually do — a held write is expected rather than an error, a
``no_data`` requirement is a gap rather than a pass, a short tool list may
mean an adapter is down rather than a smaller system. An agent told only the
happy path reports the happy path.

Layer-1 module: stdlib only.
"""

from __future__ import annotations

#: name -> (one-line description, the instruction body)
WORKFLOWS: dict[str, tuple[str, str]] = {
    "connect": (
        "Find the gateway to point this plugin at",
        "Work out which MetaForge gateway this machine should use.\n\n"
        "If the `forge` CLI is installed, run `forge connect`. It probes the "
        "usual local endpoints plus anything already configured, and prints "
        "the URL to use. It exits non-zero when it finds nothing.\n\n"
        "If `forge` is not installed, POST an MCP `initialize` to each "
        "candidate and look at `result.serverInfo.name`:\n"
        "- `http://localhost:8765/mcp` (the MCP sidecar in the standard "
        "compose file)\n"
        "- `http://localhost:8000/mcp` (a gateway serving MCP itself)\n\n"
        "Only treat a candidate as the gateway when it answers and names "
        "itself `metaforge-mcp`. Something listening on the port is not the "
        "same thing: a dev machine has plenty of services, and another MCP "
        "server will answer `initialize` perfectly happily. A 401 or 403 "
        "*is* a match -- the URL is right and a token is the next step.\n\n"
        "Then tell the user what to do with it: put the URL in the plugin's "
        "`gateway_url` setting. If nothing was found, say so and give them "
        "the three options -- start one with `docker compose up gateway`, "
        "enter a team or hosted URL, or install the `metaforge-local` "
        "package, which runs MetaForge as a local process and needs no "
        "gateway at all. Do not guess a URL on their behalf.",
    ),
    "new": (
        "Start a project, or open the one the user means",
        "Create or open a MetaForge project from what the user said.\n\n"
        "If they named an existing project ('open the arm project'), call "
        "`project.open` with their words as `query`. It resolves an id, an "
        "exact name, or a unique name substring. Several matches comes back "
        "as an error listing them — ask which one, do not pick.\n\n"
        "If they described something new ('new project: 6-DOF arm, 1 kg "
        "payload'), call `project.open` first anyway to check it does not "
        "already exist; a second project with the same subject splits the "
        "design thread in two and nothing reconciles them. Only on "
        "`no project matches` call `project.create`, with a short `name` and "
        "the user's own words as `description`.\n\n"
        "Both tools scope the session to the project. Check `scope_bound`: "
        "false means you must pass `project_id` explicitly on every later "
        "call.\n\n"
        "Then record what they already told you. Figures in the intent — '1 "
        "kg payload', '6-DOF', a reach, a duty cycle — are requirements, and "
        "belong in the twin as constraints rather than only in the chat, or "
        "the first gate has nothing to check against. Do not invent values "
        "they did not give, and do not round the ones they did.\n\n"
        "Give each one a `metric`, `operator`, `limit` and `unit` so a gate "
        "can evaluate it, plus `verification_method` (how it will be shown "
        "to hold) and `acceptance_criteria` (what counts as meeting it). "
        "`twin.record_constraint_set` returns `incomplete` naming anything "
        "you left out — read it. A requirement with no limit or no "
        "verification method is recorded but uncheckable, and in the matrix "
        "it is indistinguishable from one still waiting for evidence. If "
        "the user has not said enough to fill a field, ask rather than "
        "guessing a plausible value.",
    ),
    "use": (
        "Pick the project to work in for this session",
        "Set the active MetaForge project.\n\n"
        "Call `project.list` to show the projects on this gateway, ask which one "
        "if the user has not said, then call `session.start` with that "
        "`project_id` so everything recorded afterwards is attributed to it.\n\n"
        "Read `project_scope_bound` in the reply. `false` means the scope did "
        "not stick for this client: pass `project_id` explicitly on every later "
        "call that takes one, and say so once — otherwise the next few calls "
        "land on the wrong project and nothing reports it.\n\n"
        "`project.open` returns the brief inline. Summarise where the project "
        "stands — newest work first — and do not restate the whole brief. If "
        "no brief came back, read `metaforge://twin/brief/<project_id>`; if "
        "that is not available either, say so rather than describing the "
        "project from its name.",
    ),
    "status": (
        "Where this project stands right now",
        "Summarise the active project's state.\n\n"
        "Read `metaforge://twin/brief/<project_id>` and "
        "`metaforge://twin/requirements/<project_id>`.\n\n"
        "Report what is built, and which requirements are unverified. A "
        "requirement with `no_data` has no evidence at all — say so plainly. It "
        "is a gap, not a pass, and it is the thing most worth surfacing.",
    ),
    "design": (
        "Design or revise a part",
        "Design a part in the active project.\n\n"
        "Read the brief first so the part fits what already exists. Author "
        "geometry through the CAD tools, give every part a meaningful name "
        "(never `Part_1`), and commit with `twin.commit_geometry`.\n\n"
        "A write may be held for approval — that is expected, not an error. Tell "
        "the user it is waiting in the dashboard rather than retrying.",
    ),
    "fea": (
        "Run a load case and record the evidence",
        "Run structural analysis on a committed part.\n\n"
        "Stage the geometry with `twin.stage_work_product_file`, set up the load "
        "case, run `calculix.run_fea`, then check convergence with "
        "`calculix.check_mesh_convergence` and cross-check against a hand "
        "calculation where one applies.\n\n"
        "Record the result with `twin.record_evidence`, pinned to the exact "
        "revision it came from. A number with no evidence behind it is not a "
        "result — say what you could not establish rather than rounding it into "
        "a claim.",
    ),
    "flow": (
        "Plan a design flow for this project",
        "Propose a design flow tailored to this project (FORGE-400).\n\n"
        "Call `flow.list` first. The templates are real and the phase ids "
        "matter -- a flow tailored from a template you invented is refused.\n\n"
        "Then `flow.propose` with the project's intent and any recorded "
        "requirements. It returns the tailored flow, each change with its "
        "rationale, and an **approval id**.\n\n"
        "**Stop there.** The proposal is held for a person. You cannot "
        "approve it and no tool would let you -- report the changes and the "
        "approval id, say plainly that nothing runs until somebody answers, "
        "and do not poll waiting for an approval you were not given.\n\n"
        "Once a human has approved it, `flow.start_run` with the version id "
        "starts the run. Follow it with `flow.status` or by re-reading "
        "`metaforge://flow/run/<run_id>`. A phase reading `unknown` means its "
        "state could not be read -- say unknown, never 'not started yet'.",
    ),
    "gate": (
        "Review a maturity gate",
        "Review whether the active project can be promoted.\n\n"
        "Read `metaforge://twin/requirements/<project_id>` and report each "
        "required claim's status. `uncertain`, `stale` and `no_data` all block; "
        "only an approved waiver naming that requirement overrides a `fail`.\n\n"
        "`twin.attempt_promotion` refuses rather than warns. Do not pass "
        "`decided_by` -- it is not an argument and supplying it is an error. "
        "The call is always held for a person, and whoever approves it is "
        "recorded as the deciding authority.",
    ),
    "doctor": (
        "Check the connection and what is reachable",
        "Diagnose this MetaForge connection.\n\n"
        "Call the `health.check` **tool**, then list the tools and resources "
        "available to you.\n\n"
        "(This used to say `health/check`, `tools/list`, `resources/list`. "
        "Those are JSON-RPC methods, not tools -- a harness cannot call them, "
        "so doctor only worked where a shell was available and not at all in "
        "Codex, ChatGPT or claude.ai. FORGE-409.)\n\n"
        "From `health.check`, report four things and do not infer any of them:\n"
        "- `status`, and `unreachable_adapters` when it is `degraded`. A "
        "registered adapter that did not answer still contributes its tool "
        "count to `tools_registered`; `reachable` is the field that says "
        "whether those tools can be called.\n"
        "- `auth.mode`. If it is `open`, say so plainly -- every connection is "
        "accepted and no call is attributable. If it is `unknown`, the server "
        "was not told; report that rather than assuming it is secured.\n"
        "- `client.protocol_skew`, if present, with the version the client "
        "asked for and the one the server pinned.\n"
        "- `version`, the gateway version, against the version of the plugin "
        "package you are running from.\n\n"
        "An adapter whose container is down contributes no tools and no "
        "resources, and the list simply looks shorter. Name any that are "
        "missing rather than describing what is left as if it were "
        "everything -- `health.check`'s `adapters` array is the reliable "
        "source for that, since you can read it without a shell.\n\n"
        "Finally, look for MetaForge registered **twice** -- a plugin plus a "
        "user-scope server entry. If any tool name appears more than once in "
        "your own tool list, say so: calls can land on whichever gateway "
        "answers first, so two connections to different gateways will silently "
        "disagree about the twin. Only you can see this; the server cannot.",
    ),
}


def prompt_manifest() -> list[dict[str, str]]:
    """``prompts/list`` entries for every workflow."""
    return [
        {"name": name, "description": description}
        for name, (description, _body) in WORKFLOWS.items()
    ]


def prompt_body(name: str) -> str:
    """The instruction text for one workflow.

    Raises ``KeyError`` for an unknown name; the caller turns that into a
    JSON-RPC error that names the ones that exist.
    """
    return WORKFLOWS[name][1]
