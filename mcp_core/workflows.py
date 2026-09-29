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
    "use": (
        "Pick the project to work in for this session",
        "Set the active MetaForge project.\n\n"
        "Call `project.list` to show the projects on this gateway, ask which one "
        "if the user has not said, then call `session.start` with that "
        "`project_id` so everything recorded afterwards is attributed to it.\n\n"
        "Then read `metaforge://twin/brief/<project_id>` and summarise where the "
        "project stands — newest work first. Do not restate the whole brief.",
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
    "gate": (
        "Review a maturity gate",
        "Review whether the active project can be promoted.\n\n"
        "Read `metaforge://twin/requirements/<project_id>` and report each "
        "required claim's status. `uncertain`, `stale` and `no_data` all block; "
        "only an approved waiver naming that requirement overrides a `fail`.\n\n"
        "`twin.attempt_promotion` refuses rather than warns, and it needs a named "
        "human in `decided_by`. Do not supply one on the user's behalf.",
    ),
    "doctor": (
        "Check the connection and what is reachable",
        "Diagnose this MetaForge connection.\n\n"
        "Call `health/check`, then `tools/list` and `resources/list`. Report the "
        "gateway version and auth mode.\n\n"
        "Check `_meta.unavailableAdapters` on both listings — an adapter whose "
        "container is down contributes no tools and no resources, and the list "
        "simply looks shorter. Name any that are missing rather than describing "
        "what is left as if it were everything.",
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
