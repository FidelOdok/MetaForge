"""``forge memory`` subcommands (MET-453).

Today: a single ``retrieve`` command that asks the gateway for past
agent-task experiences similar to a goal string. Hits the
``POST /v1/memory/retrieve`` route directly (no ``/api/v1`` prefix —
see MET-451).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Wire ``forge memory retrieve`` into the top-level parser."""
    memory = subparsers.add_parser(
        "memory",
        help="Agent memory layer commands",
    )
    memory_sub = memory.add_subparsers(
        dest="memory_command",
        help="Subcommands",
    )

    retrieve = memory_sub.add_parser(
        "retrieve",
        help="Find past agent-task experiences similar to a goal",
    )
    retrieve.add_argument(
        "goal",
        help='Natural-language goal, e.g. "validate stress on titanium bracket".',
    )
    retrieve.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of hits to return (1-50). Default 5.",
    )
    retrieve.add_argument(
        "--project-id",
        dest="project_id",
        default=None,
        help="Optional project scope (UUID).",
    )
    retrieve.add_argument(
        "--agent-code",
        dest="agent_code",
        default=None,
        help="Optional filter to a specific agent_code.",
    )
    only_success_group = retrieve.add_mutually_exclusive_group()
    only_success_group.add_argument(
        "--only-success",
        dest="only_success",
        action="store_const",
        const=True,
        default=None,
        help="Only return experiences from successful runs.",
    )
    only_success_group.add_argument(
        "--only-failure",
        dest="only_success",
        action="store_const",
        const=False,
        help="Only return experiences from failed runs.",
    )


def handle_memory(args: argparse.Namespace, client: ForgeClient) -> dict[str, Any] | None:
    """Dispatch the ``memory`` subcommand."""
    if args.memory_command == "retrieve":
        return _run_retrieve(args, client)
    return {
        "error": "missing memory subcommand",
        "hint": "see `forge memory --help`",
    }


def _run_retrieve(args: argparse.Namespace, client: ForgeClient) -> dict[str, Any] | None:
    """Call ``POST /v1/memory/retrieve`` and print the results.

    Sidesteps ``ForgeClient._url`` (which prepends ``/api/v1``) because
    memory routes — like knowledge — sit at ``/v1/memory/...`` after the
    MET-451 prefix move.
    """
    import httpx

    payload: dict[str, Any] = {
        "goal": args.goal,
        "limit": args.limit,
    }
    if args.project_id is not None:
        payload["projectId"] = args.project_id
    if args.agent_code is not None:
        payload["agentCode"] = args.agent_code
    if args.only_success is not None:
        payload["onlySuccess"] = args.only_success

    try:
        with httpx.Client(base_url=client.base_url, timeout=client.timeout) as http:
            resp = http.post("/v1/memory/retrieve", json=payload)
    except httpx.HTTPError as exc:
        print(f"Error contacting gateway: {exc}", file=sys.stderr)
        sys.exit(2)

    if resp.status_code == 503:
        detail = _safe_detail(resp)
        print(
            f"Memory service not ready ({detail}). "
            "Run the gateway with an embedding service configured.",
            file=sys.stderr,
        )
        sys.exit(3)
    if resp.status_code != 200:
        print(
            f"Gateway returned {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        raise ForgeClientError(
            f"memory.retrieve failed with status {resp.status_code}",
            status_code=resp.status_code,
        )

    body = resp.json()
    hits = body.get("hits", [])
    return {
        "query": body.get("query"),
        "total_found": body.get("totalFound", len(hits)),
        "hits": [
            {
                "rank": hit.get("rank"),
                "similarity": hit.get("similarity"),
                "agent_code": hit.get("agentCode"),
                "success": hit.get("success"),
                "result_summary": hit.get("resultSummary"),
                "experience_id": hit.get("experienceId"),
            }
            for hit in hits
        ],
    }


def _safe_detail(resp: Any) -> str:
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return resp.text or "no detail"
    detail = body.get("detail")
    return str(detail) if detail else "no detail"
