"""MetaForge Python CLI entry point.

Usage::

    python -m cli.forge_cli.main run validate_stress --work_product <uuid> --params '{"load": 500}'
    python -m cli.forge_cli.main status <session-id>
    python -m cli.forge_cli.main twin query <node-id>
    python -m cli.forge_cli.main twin list --domain mechanical --type cad_model
    python -m cli.forge_cli.main proposals
    python -m cli.forge_cli.main approve <change-id> --reason "looks good"
    python -m cli.forge_cli.main reject <change-id> --reason "needs revision"

No external dependencies beyond stdlib + httpx are required.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cli.forge_cli.chat import handle_chat
from cli.forge_cli.client import ForgeClient
from cli.forge_cli.codex_login import handle_codex_login
from cli.forge_cli.codex_login import register_subparser as register_codex_login_subparser
from cli.forge_cli.formatters import format_output
from cli.forge_cli.knowledge import handle_knowledge
from cli.forge_cli.knowledge import register_subparser as register_knowledge_subparser
from cli.forge_cli.memory import handle_memory
from cli.forge_cli.memory import register_subparser as register_memory_subparser
from cli.forge_cli.routines import handle_routine
from cli.forge_cli.runs import handle_runs
from cli.forge_cli.sources import handle_sources
from cli.forge_cli.sources import register_subparser as register_sources_subparser

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
        default="ask",
        help="Proposal handling: ask (prompt), auto (approve), plan (hold). Default ask",
    )
    chat_parser.add_argument(
        "--hooks",
        default=".forge/hooks.json",
        help="Path to a lifecycle-hooks config (default .forge/hooks.json)",
    )
    chat_parser.add_argument("--no-hooks", action="store_true", help="Disable lifecycle hooks")

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


def handle_twin(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge twin query|list``."""
    if args.twin_command == "query":
        return client.twin_query(args.node_id)
    if args.twin_command == "list":
        return client.twin_list(domain=args.domain, work_product_type=args.work_product_type)
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


_HANDLERS = {
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
    "chat": handle_chat,
    "routine": handle_routine,
    "codex-login": handle_codex_login,
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

    client = ForgeClient(base_url=args.gateway_url)

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
