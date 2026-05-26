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

    consolidate = memory_sub.add_parser(
        "consolidate",
        help="Trigger a consolidation pass (synthesize insights from experiences)",
    )
    consolidate.add_argument(
        "--mode",
        choices=["background", "on_demand", "proactive", "janitor"],
        default="on_demand",
        help=(
            "Consolidation mode. Default on_demand (manual triage, relaxed "
            "importance floor). proactive requires --project-id; janitor "
            "re-validates existing insights without synthesizing new ones."
        ),
    )
    consolidate.add_argument(
        "--project-id",
        dest="project_id",
        default=None,
        help="Project scope (UUID). Required for --mode proactive.",
    )
    consolidate.add_argument(
        "--theme",
        default=None,
        help="Optional theme filter (e.g. mechanical_validation, power_analysis).",
    )
    consolidate.add_argument(
        "--min-importance",
        dest="min_importance",
        type=float,
        default=None,
        help="Override the importance floor (0.0-1.0).",
    )
    consolidate.add_argument(
        "--limit",
        dest="fetch_limit",
        type=int,
        default=None,
        help="Maximum number of experiences to fetch for this pass.",
    )

    insights = memory_sub.add_parser(
        "insights",
        help="List consolidated insights (synthesized lessons)",
    )
    insights.add_argument(
        "--theme",
        default=None,
        help="Optional theme filter (e.g. mechanical_validation, power_analysis).",
    )
    insights.add_argument(
        "--include-stale",
        dest="include_stale",
        action="store_true",
        help="Include STALE_WARN insights (default: excluded — fresh only).",
    )
    insights.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of insights to return (1-500). Default 50.",
    )


def handle_memory(args: argparse.Namespace, client: ForgeClient) -> dict[str, Any] | None:
    """Dispatch the ``memory`` subcommand."""
    if args.memory_command == "retrieve":
        return _run_retrieve(args, client)
    if args.memory_command == "consolidate":
        return _run_consolidate(args, client)
    if args.memory_command == "insights":
        return _run_insights(args, client)
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


def _run_consolidate(args: argparse.Namespace, client: ForgeClient) -> dict[str, Any] | None:
    """Call ``POST /v1/memory/consolidate`` and return the consolidation report."""
    import httpx

    payload: dict[str, Any] = {"mode": args.mode}
    if args.project_id is not None:
        payload["projectId"] = args.project_id
    if args.theme is not None:
        payload["theme"] = args.theme
    if args.min_importance is not None:
        payload["minImportance"] = args.min_importance
    if args.fetch_limit is not None:
        payload["fetchLimit"] = args.fetch_limit

    try:
        with httpx.Client(base_url=client.base_url, timeout=client.timeout) as http:
            resp = http.post("/v1/memory/consolidate", json=payload)
    except httpx.HTTPError as exc:
        print(f"Error contacting gateway: {exc}", file=sys.stderr)
        sys.exit(2)

    if resp.status_code == 503:
        detail = _safe_detail(resp)
        print(
            f"Consolidation service not ready ({detail}). "
            "Run the gateway with the memory layer configured.",
            file=sys.stderr,
        )
        sys.exit(3)
    if resp.status_code == 422:
        detail = _safe_detail(resp)
        print(f"Invalid consolidation request: {detail}", file=sys.stderr)
        sys.exit(4)
    if resp.status_code != 200:
        print(
            f"Gateway returned {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        raise ForgeClientError(
            f"memory.consolidate failed with status {resp.status_code}",
            status_code=resp.status_code,
        )

    body = resp.json()
    return {
        "mode": body.get("mode"),
        "fetched_count": body.get("fetchedCount", 0),
        "group_count": body.get("groupCount", 0),
        "synthesized_count": body.get("synthesizedCount", 0),
        "accepted_count": body.get("acceptedCount", 0),
        "rejected_count": body.get("rejectedCount", 0),
        "revalidated_count": body.get("revalidatedCount", 0),
        "newly_failed_count": body.get("newlyFailedCount", 0),
        "rejected_reasons": body.get("rejectedReasons", []),
    }


def _run_insights(args: argparse.Namespace, client: ForgeClient) -> dict[str, Any] | None:
    """Call ``GET /v1/memory/insights`` and return the consolidated insights."""
    import httpx

    params: dict[str, Any] = {"limit": args.limit}
    if args.theme is not None:
        params["theme"] = args.theme
    if args.include_stale:
        params["includeStale"] = "true"

    try:
        with httpx.Client(base_url=client.base_url, timeout=client.timeout) as http:
            resp = http.get("/v1/memory/insights", params=params)
    except httpx.HTTPError as exc:
        print(f"Error contacting gateway: {exc}", file=sys.stderr)
        sys.exit(2)

    if resp.status_code == 503:
        detail = _safe_detail(resp)
        print(
            f"Insight store not ready ({detail}). "
            "Run the gateway with the memory layer configured.",
            file=sys.stderr,
        )
        sys.exit(3)
    if resp.status_code != 200:
        print(
            f"Gateway returned {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        raise ForgeClientError(
            f"memory.insights failed with status {resp.status_code}",
            status_code=resp.status_code,
        )

    body = resp.json()
    insights = body.get("insights", [])
    return {
        "total": body.get("total", len(insights)),
        "theme": body.get("theme"),
        "include_stale": body.get("includeStale", False),
        "insights": [
            {
                "id": i.get("id"),
                "theme": i.get("theme"),
                "kind": i.get("kind"),
                "status": i.get("status"),
                "confidence": i.get("confidence"),
                "narrative": i.get("narrative"),
            }
            for i in insights
        ],
    }


def _safe_detail(resp: Any) -> str:
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return resp.text or "no detail"
    detail = body.get("detail")
    return str(detail) if detail else "no detail"
