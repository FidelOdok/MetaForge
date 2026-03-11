"""MetaForge Python CLI entry point.

Usage::

    python -m cli.forge_cli.main run validate_stress --artifact <uuid> --params '{"load": 500}'
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

from cli.forge_cli.client import ForgeClient
from cli.forge_cli.formatters import format_output

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
    run_parser.add_argument("--artifact", required=True, help="UUID of the target artifact")
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

    twin_list = twin_sub.add_parser("list", help="List twin artifacts")
    twin_list.add_argument("--domain", default=None, help="Filter by domain")
    twin_list.add_argument("--type", default=None, dest="artifact_type", help="Filter by type")

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
        artifact_id=args.artifact,
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
        return client.twin_list(domain=args.domain, artifact_type=args.artifact_type)
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

_HANDLERS = {
    "run": handle_run,
    "status": handle_status,
    "twin": handle_twin,
    "proposals": handle_proposals,
    "approve": handle_approve,
    "reject": handle_reject,
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

    output = format_output(result, fmt=args.output_format)
    print(output)


if __name__ == "__main__":
    main()
