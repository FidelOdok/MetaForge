"""MetaForge Python CLI entry point.

Usage::

    python -m cli.forge_cli.main run validate_stress --work_product <uuid> --params '{"load": 500}'
    python -m cli.forge_cli.main status <session-id>
    python -m cli.forge_cli.main twin query <node-id>
    python -m cli.forge_cli.main twin list --domain mechanical --type cad_model
    python -m cli.forge_cli.main twin list --project "6-DOF Robotic Arm"
    python -m cli.forge_cli.main proposals
    python -m cli.forge_cli.main approve <change-id> --reason "looks good"
    python -m cli.forge_cli.main reject <change-id> --reason "needs revision"

No external dependencies beyond stdlib + httpx are required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from cli.forge_cli.auth import handle_auth
from cli.forge_cli.auth import register_subparser as register_auth_subparser
from cli.forge_cli.cad import handle_cad
from cli.forge_cli.chat import handle_chat
from cli.forge_cli.client import ForgeClient
from cli.forge_cli.codex_login import handle_codex_login
from cli.forge_cli.codex_login import register_subparser as register_codex_login_subparser
from cli.forge_cli.config import ForgeConfig, handle_config
from cli.forge_cli.formatters import format_output
from cli.forge_cli.knowledge import handle_knowledge
from cli.forge_cli.knowledge import register_subparser as register_knowledge_subparser
from cli.forge_cli.memory import handle_memory
from cli.forge_cli.memory import register_subparser as register_memory_subparser
from cli.forge_cli.projects import handle_projects
from cli.forge_cli.routines import handle_routine
from cli.forge_cli.runs import handle_design, handle_runs
from cli.forge_cli.sources import handle_sources
from cli.forge_cli.sources import register_subparser as register_sources_subparser
from cli.forge_cli.tunnel import TUNNEL_COMMANDS

# ---------------------------------------------------------------------------
# Argument parser construction
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="forge",
        description="MetaForge CLI — interact with the Gateway API",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "compact"],
        default="table",
        dest="output_format",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--gateway-url",
        default=None,
        help="Gateway base URL (default: METAFORGE_GATEWAY_URL or http://localhost:8000)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- tunnel ------------------------------------------------------------
    tunnel_parser = subparsers.add_parser(
        "tunnel",
        help="Expose a local gateway to a cloud harness through a tunnel",
    )
    tunnel_sub = tunnel_parser.add_subparsers(dest="tunnel_command")
    tunnel_up = tunnel_sub.add_parser("up", help="Check the gateway, then open a tunnel")
    tunnel_up.add_argument(
        "--url",
        default="http://localhost:8765/mcp",
        help="The local MCP endpoint to expose (default: http://localhost:8765/mcp).",
    )
    tunnel_up.add_argument(
        "--port", type=int, default=8765, help="Local port to forward (default: 8765)."
    )
    tunnel_up.add_argument(
        "--provider",
        default=None,
        choices=sorted(TUNNEL_COMMANDS),
        help="Which tunnel to use. Default: whichever is installed.",
    )
    tunnel_up.add_argument(
        "--check-only",
        action="store_true",
        help="Run the pre-flight and stop, without opening anything.",
    )

    # -- connect -----------------------------------------------------------
    connect_parser = subparsers.add_parser(
        "connect",
        help="Find a local MetaForge gateway, or say what to enter instead",
    )
    connect_parser.add_argument(
        "--url",
        default=None,
        help="Check one specific URL instead of the usual local candidates.",
    )
    connect_parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Seconds to wait per candidate (default: 2).",
    )

    # -- run ---------------------------------------------------------------
    run_parser = subparsers.add_parser("run", help="Invoke a skill via the gateway")
    run_parser.add_argument("skill_name", help="Name of the skill to invoke")
    run_parser.add_argument("--work_product", required=True, help="UUID of the target work_product")
    run_parser.add_argument(
        "--params",
        default="{}",
        help='JSON parameters (default: "{}")',
    )
    run_parser.add_argument("--session-id", default=None, help="Session UUID")

    # -- status ------------------------------------------------------------
    status_parser = subparsers.add_parser("status", help="Show session/agent status")
    status_parser.add_argument("session_id", help="Session UUID")

    # -- twin --------------------------------------------------------------
    twin_parser = subparsers.add_parser("twin", help="Digital Twin queries")
    twin_sub = twin_parser.add_subparsers(dest="twin_command", help="Twin subcommands")

    twin_query = twin_sub.add_parser("query", help="Query a single twin node")
    twin_query.add_argument("node_id", help="Node UUID")

    twin_list = twin_sub.add_parser("list", help="List twin work_products")
    twin_list.add_argument("--domain", default=None, help="Filter by domain")
    twin_list.add_argument("--type", default=None, dest="work_product_type", help="Filter by type")
    twin_list.add_argument(
        "--project", default=None, help="Filter by project id or name (FORGE-248)"
    )

    # -- proposals ---------------------------------------------------------
    subparsers.add_parser("proposals", help="List pending change proposals")

    # -- approve -----------------------------------------------------------
    approve_parser = subparsers.add_parser("approve", help="Approve a change proposal")
    approve_parser.add_argument("change_id", help="Change proposal UUID")
    approve_parser.add_argument("--reason", required=True, help="Approval reason")
    approve_parser.add_argument("--reviewer", default="cli-user", help="Reviewer identity")

    # -- reject ------------------------------------------------------------
    reject_parser = subparsers.add_parser("reject", help="Reject a change proposal")
    reject_parser.add_argument("change_id", help="Change proposal UUID")
    reject_parser.add_argument("--reason", required=True, help="Rejection reason")
    reject_parser.add_argument("--reviewer", default="cli-user", help="Reviewer identity")

    # -- runs (harness) ----------------------------------------------------
    runs_parser = subparsers.add_parser("runs", help="Drive harness runs (/v1/runs)")
    runs_sub = runs_parser.add_subparsers(dest="runs_command", help="Runs subcommands")

    runs_create = runs_sub.add_parser("create", help="Create a run")
    runs_create.add_argument("--goal", default=None, help="Run goal text")
    runs_create.add_argument("--request-json", default=None, help="Full run request as JSON")
    runs_create.add_argument("--no-start", action="store_true", help="Leave the run queued")

    runs_list = runs_sub.add_parser("list", help="List runs")
    runs_list.add_argument("--json", action="store_true", help="JSON output")

    runs_get = runs_sub.add_parser("get", help="Fetch one run")
    runs_get.add_argument("run_id", help="Run id")
    runs_get.add_argument("--json", action="store_true", help="JSON output")

    runs_approve = runs_sub.add_parser("approve", help="Approve a paused run")
    runs_approve.add_argument("run_id", help="Run id")

    runs_reject = runs_sub.add_parser("reject", help="Reject a paused run")
    runs_reject.add_argument("run_id", help="Run id")

    runs_watch = runs_sub.add_parser("watch", help="Stream a run's status (SSE)")
    runs_watch.add_argument("run_id", help="Run id")

    # -- projects ----------------------------------------------------------
    projects_parser = subparsers.add_parser("projects", help="List/inspect/delete projects")
    projects_sub = projects_parser.add_subparsers(
        dest="projects_command", help="Projects subcommands"
    )
    projects_list = projects_sub.add_parser("list", help="List projects")
    projects_list.add_argument("--json", action="store_true", help="JSON output")
    projects_list.add_argument("--status", default=None, help="Filter by status (e.g. draft)")
    projects_get = projects_sub.add_parser("get", help="Fetch one project")
    projects_get.add_argument("project_id", help="Project id")
    projects_get.add_argument("--json", action="store_true", help="JSON output")
    projects_delete = projects_sub.add_parser("delete", help="Delete a project")
    projects_delete.add_argument("project_id", help="Project id")
    projects_delete.add_argument("--yes", action="store_true", help="Skip confirmation")

    # -- cad (deterministic multi-part assembly authoring) -----------------
    cad_parser = subparsers.add_parser("cad", help="Author CAD assemblies from a spec")
    cad_sub = cad_parser.add_subparsers(dest="cad_command", help="CAD subcommands")
    cad_build = cad_sub.add_parser("build", help="Build + commit a multi-part assembly")
    cad_build.add_argument("spec", help="Path to an assembly spec JSON ({name, parts, ...})")
    cad_build.add_argument("--project-id", default=None, help="Scope the cad_model to a project")
    cad_ft = cad_sub.add_parser(
        "from-text", help="Compile a plain-English description into an assembly + build it"
    )
    cad_ft.add_argument("description", help="e.g. 'a 100x100x6 plate with 4 M3 corner holes'")
    cad_ft.add_argument("--name", default=None, help="Override the assembly name")
    cad_ft.add_argument("--project-id", default=None, help="Scope the cad_model to a project")
    cad_ft.add_argument(
        "--dry-run",
        action="store_true",
        help="Compile + print the spec for review without building it",
    )
    cad_ft.add_argument("--provider", default=None, help="LLM provider override for translation")
    cad_ft.add_argument("--model", default=None, help="LLM model override for translation")

    # -- design (friendly wrapper over `runs create` for a gated flow) -----
    design_parser = subparsers.add_parser(
        "design", help="Start a gated design flow for a product goal"
    )
    design_parser.add_argument("goal", help="What to design (e.g. 'an I2C IMU breakout board')")
    design_parser.add_argument(
        "--flow",
        default="hardware_v1",
        choices=["hardware_v1", "mech_v1", "design_v1"],
        help="Design flow to run (default: hardware_v1)",
    )
    design_parser.add_argument("--project-id", default=None, help="Scope the run to a project")
    design_parser.add_argument("--no-start", action="store_true", help="Create but don't start")
    design_parser.add_argument("--no-watch", action="store_true", help="Don't stream transitions")

    # -- chat --------------------------------------------------------------
    chat_parser = subparsers.add_parser(
        "chat", help="Interactive assistant REPL (thin client over /v1/chat)"
    )
    chat_parser.add_argument(
        "--message", "-m", default=None, help="Send a single message and exit (one-shot mode)"
    )
    chat_parser.add_argument("--thread", default=None, help="Reuse an existing thread id")
    chat_parser.add_argument(
        "--session", default=None, help="Scope entity id for a new thread (default: random)"
    )
    chat_parser.add_argument(
        "--project",
        default=None,
        help="Scope the chat to a project id: the agent sees the project's work "
        "products and saves new CAD/decisions into it",
    )
    chat_parser.add_argument("--title", default=None, help="Title for a new thread")
    chat_parser.add_argument("--provider", default=None, help="Override provider for the turn")
    chat_parser.add_argument("--model", default=None, help="Override model for the turn")
    chat_parser.add_argument(
        "--timeout", type=float, default=120.0, help="Per-turn timeout in seconds (default 120)"
    )
    chat_parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    chat_parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable SSE streaming; use request/refetch instead",
    )
    chat_parser.add_argument(
        "--mode",
        choices=["ask", "auto", "plan"],
        default=None,
        help="Proposal handling: ask (prompt), auto (approve), plan (hold). "
        "Default: config's mode, else ask",
    )
    chat_parser.add_argument(
        "--hooks",
        default=".forge/hooks.json",
        help="Path to a lifecycle-hooks config (default .forge/hooks.json)",
    )
    chat_parser.add_argument("--no-hooks", action="store_true", help="Disable lifecycle hooks")

    # -- config (client-side settings + wizard) ----------------------------
    config_parser = subparsers.add_parser(
        "config", help="Configure the CLI (gateway, provider, model) — wizard by default"
    )
    config_sub = config_parser.add_subparsers(dest="config_command", help="Config subcommands")
    config_sub.add_parser("show", help="Print the current configuration")
    config_sub.add_parser("path", help="Print the config file path")
    config_set = config_sub.add_parser("set", help="Set one value")
    config_set.add_argument("key", help="gateway_url | provider | model | mode")
    config_set.add_argument("value", help="Value to set")

    # -- routine (scheduled background runs) -------------------------------
    routine_parser = subparsers.add_parser("routine", help="Scheduled background chat runs")
    routine_parser.add_argument(
        "--file", default=".forge/routines.json", help="Routines store path"
    )
    routine_sub = routine_parser.add_subparsers(dest="routine_command", help="Routine subcommands")

    routine_add = routine_sub.add_parser("add", help="Add a scheduled routine")
    routine_add.add_argument("prompt", help="Prompt to run on schedule")
    routine_add.add_argument("--every", required=True, help="Interval, e.g. 30s, 10m, 2h, 1d")
    routine_add.add_argument("--provider", default=None, help="Provider override")
    routine_add.add_argument("--model", default=None, help="Model override")
    routine_add.add_argument(
        "--mode", choices=["ask", "auto", "plan"], default="ask", help="Proposal handling mode"
    )

    routine_sub.add_parser("list", help="List routines")

    routine_remove = routine_sub.add_parser("remove", help="Remove a routine")
    routine_remove.add_argument("routine_id", help="Routine id")

    routine_sub.add_parser("run-due", help="Run all routines whose interval has elapsed")

    # -- ingest ------------------------------------------------------------
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest markdown / PDF docs into the L1 knowledge layer",
    )
    ingest_parser.add_argument(
        "path",
        help="File or directory to ingest. Directories walk recursively by default.",
    )
    ingest_parser.add_argument(
        "--type",
        dest="knowledge_type",
        default=None,
        help=(
            "Knowledge type for every file in this run (one of: "
            "design_decision, component, failure, constraint, session). "
            "If omitted, the CLI infers per-file from the path."
        ),
    )
    ingest_parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="When ingesting a directory, only consider its immediate children.",
    )
    ingest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be ingested without making any HTTP calls.",
    )
    ingest_parser.add_argument(
        "--work-product",
        dest="work_product",
        default=None,
        help="Optional source_work_product_id (UUID) tagged on every ingested doc.",
    )
    ingest_parser.add_argument(
        "--metadata",
        default=None,
        help="JSON object of extra metadata round-tripped on search hits.",
    )
    ingest_parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help=(
            "Per-request HTTP timeout in seconds. Override with METAFORGE_INGEST_TIMEOUT env var."
        ),
    )

    # -- sources -----------------------------------------------------------
    register_sources_subparser(subparsers)

    # -- knowledge (MET-443) ----------------------------------------------
    register_knowledge_subparser(subparsers)

    # -- memory (MET-453) -------------------------------------------------
    register_memory_subparser(subparsers)

    # -- codex-login (MET-550) --------------------------------------------
    register_codex_login_subparser(subparsers)
    register_auth_subparser(subparsers)

    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _parse_params(raw: str) -> dict[str, Any]:
    """Parse a JSON string into a dict, raising a friendly error on failure."""
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in --params: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(result, dict):
        print("Error: --params must be a JSON object", file=sys.stderr)
        sys.exit(1)
    return result


def handle_run(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge run <skill>``."""
    params = _parse_params(args.params)
    return client.run_skill(
        skill_name=args.skill_name,
        work_product_id=args.work_product,
        parameters=params,
        session_id=args.session_id,
    )


def handle_status(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge status <session_id>``."""
    return client.get_status(args.session_id)


def _resolve_project_ref(client: ForgeClient, project_ref: str) -> str:
    """Accept either a project UUID or a project name for ``--project`` (FORGE-248).

    A name is resolved via ``GET /v1/projects`` (case-insensitive exact
    match) -- the gateway's own node-list filter only understands a real
    project id.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(project_ref)
        return project_ref
    except ValueError:
        pass

    projects = client.list_projects().get("projects", [])
    matches = [p for p in projects if str(p.get("name", "")).lower() == project_ref.lower()]
    if not matches:
        print(f"Error: no project named {project_ref!r} found", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(
            f"Error: {len(matches)} projects are named {project_ref!r} -- use the project id "
            "instead to disambiguate",
            file=sys.stderr,
        )
        sys.exit(1)
    return str(matches[0]["id"])


def handle_twin(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge twin query|list``."""
    if args.twin_command == "query":
        return client.twin_query(args.node_id)
    if args.twin_command == "list":
        project_id = _resolve_project_ref(client, args.project) if args.project else None
        return client.twin_list(
            domain=args.domain, work_product_type=args.work_product_type, project_id=project_id
        )
    print("Error: specify a twin subcommand (query or list)", file=sys.stderr)
    sys.exit(1)


def handle_proposals(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge proposals``."""
    return client.list_proposals()


def handle_approve(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge approve <change_id>``."""
    return client.approve_proposal(
        change_id=args.change_id,
        reason=args.reason,
        reviewer=args.reviewer,
    )


def handle_reject(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge reject <change_id>``."""
    return client.reject_proposal(
        change_id=args.change_id,
        reason=args.reason,
        reviewer=args.reviewer,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def handle_ingest(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge ingest <path>``."""
    from cli.forge_cli.ingest import handle_ingest as _do_ingest

    return _do_ingest(args, client)


def handle_tunnel(args: argparse.Namespace, client: ForgeClient) -> None:
    """`forge tunnel up` (FORGE-387).

    The pre-flight is the reason this exists rather than a line in the
    README telling people to run cloudflared themselves. A tunnel does
    not change what the server enforces; it changes who can reach it,
    and the moment between "it worked locally" and "it is public" is
    exactly where nobody re-checks.
    """
    import subprocess

    from cli.forge_cli.tunnel import preflight, tunnel_command

    if getattr(args, "tunnel_command", None) != "up":
        print("usage: forge tunnel up [--url URL] [--port PORT]", file=sys.stderr)
        sys.exit(1)

    result = preflight(args.url)
    report = result.report()
    if report:
        print(report, file=sys.stderr if result.blockers else sys.stdout)
    if not result.ok:
        sys.exit(1)
    print(f"Gateway at {args.url} is ready to expose.")
    if args.check_only:
        return

    found = tunnel_command(args.port, prefer=args.provider)
    if found is None:
        installed = ", ".join(sorted(TUNNEL_COMMANDS))
        print(
            f"No tunnel client found. Install one of: {installed}.\n"
            "Not bundled on purpose -- a binary that opens a public hostname "
            "is something you should choose to have.",
            file=sys.stderr,
        )
        sys.exit(1)
    name, argv = found
    print(f"Starting {name}: {' '.join(argv)}")
    print("Give the public URL it prints to your harness as the gateway URL.")
    # Handed over rather than wrapped: the tunnel's own output is what the
    # user needs (the hostname, the connection state), and re-printing it
    # through here would only lose detail.
    raise SystemExit(subprocess.call(argv))


def handle_connect(args: argparse.Namespace, client: ForgeClient) -> None:
    """Guided connect (FORGE-329).

    Takes a ``client`` only to match every other handler's signature and
    does not use it: this is the one command that has to work when there
    is no gateway to talk to, which is the situation it exists for.

    Exits non-zero when nothing was found, so it is usable as a
    precondition check in a script and not only by eye.
    """
    from cli.forge_cli.discover import candidates_for, describe, detect_gateway

    if args.url:
        candidates: tuple[str, ...] = (args.url,)
    else:
        # Whatever this CLI is already configured to talk to comes first:
        # someone who has set METAFORGE_GATEWAY_URL or saved a config has
        # already told us where their gateway is, and probing localhost
        # ahead of it could hand them a different one.
        candidates = candidates_for(getattr(client, "base_url", "") or "")
    found, probes = detect_gateway(candidates, timeout=args.timeout)
    if getattr(args, "format", "table") == "json":
        print(
            json.dumps(
                {
                    "found": found.url if found else None,
                    "probes": [
                        {
                            "url": p.url,
                            "reachable": p.reachable,
                            "is_metaforge": p.is_metaforge,
                            "requires_auth": p.requires_auth,
                            "detail": p.detail,
                        }
                        for p in probes
                    ],
                },
                indent=2,
            )
        )
    else:
        print(describe(found, probes))
    if found is None:
        sys.exit(1)


_HANDLERS = {
    "connect": handle_connect,
    "tunnel": handle_tunnel,
    "run": handle_run,
    "status": handle_status,
    "twin": handle_twin,
    "proposals": handle_proposals,
    "approve": handle_approve,
    "reject": handle_reject,
    "ingest": handle_ingest,
    "sources": handle_sources,
    "knowledge": handle_knowledge,
    "memory": handle_memory,
    "runs": handle_runs,
    "projects": handle_projects,
    "cad": handle_cad,
    "design": handle_design,
    "chat": handle_chat,
    "routine": handle_routine,
    "config": handle_config,
    "codex-login": handle_codex_login,
    "auth": handle_auth,
}


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    # Client-side config, in the conventional order: explicit --gateway-url,
    # then METAFORGE_GATEWAY_URL, then the saved config, then ForgeClient's
    # own default. The loaded config is attached to args so handlers (e.g.
    # chat) can read default provider/model.
    #
    # MET-729: the env var used to sit BELOW the saved config, because
    # ForgeClient only consults it when base_url is falsy and the saved value
    # was passed in unconditionally. So a caller that set
    # METAFORGE_GATEWAY_URL to isolate itself was silently ignored, which is
    # backwards -- an environment variable exists to override persisted
    # config for one invocation. Live consequence: a unit test that pinned
    # the URL to a dead port ingested documents into the shared dev gateway
    # on every run (11 knowledge_document_ingested events in a week, from
    # pytest temp paths, plus 18 lightrag_ingest_not_persisted errors).
    config = ForgeConfig.load()
    args.forge_config = config
    effective_gateway = (
        args.gateway_url or os.environ.get("METAFORGE_GATEWAY_URL") or config.gateway_url
    )
    client = ForgeClient(base_url=effective_gateway)

    try:
        result = handler(args, client)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Handlers that print directly to stdout (sources, etc.) return
    # ``None`` so the dispatcher doesn't double-render their output.
    if result is None:
        return

    output = format_output(result, fmt=args.output_format)
    print(output)


if __name__ == "__main__":
    main()
