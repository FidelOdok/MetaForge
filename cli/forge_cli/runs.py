"""`forge runs` command handlers (MET-548).

Drives the harness Runs API (`/v1/runs`): create, list, get, approve/reject a
paused run, and `watch` a run's live SSE status stream.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError, ForgeClientNotFound
from cli.forge_cli.formatters import format_output

_LIST_COLUMNS = ["id", "status", "created_at", "updated_at"]


def _fmt(args: argparse.Namespace) -> str:
    return "json" if getattr(args, "json", False) else "table"


def handle_runs(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge runs <subcommand>`."""
    sub = args.runs_command
    if sub == "create":
        return _create(args, client)
    if sub == "list":
        return _list(args, client)
    if sub == "get":
        return _get(args, client)
    if sub in ("approve", "reject"):
        return _approval(args, client, sub)
    if sub == "watch":
        return _watch(args, client)
    print("Error: unknown runs subcommand", file=sys.stderr)
    return None


def _create(args: argparse.Namespace, client: ForgeClient) -> Any:
    request: dict[str, Any] = {}
    if args.goal:
        request["goal"] = args.goal
    if args.request_json:
        try:
            request = json.loads(args.request_json)
        except json.JSONDecodeError as exc:
            print(f"Error: --request-json is not valid JSON: {exc}", file=sys.stderr)
            return None
    try:
        run = client.create_run(request, start=not args.no_start)
    except ForgeClientError as exc:
        print(f"Error: failed to create run: {exc}", file=sys.stderr)
        return None
    print(format_output(run, fmt="json"))
    return None


def _list(args: argparse.Namespace, client: ForgeClient) -> Any:
    try:
        payload = client.list_runs()
    except ForgeClientError as exc:
        print(f"Error: failed to list runs: {exc}", file=sys.stderr)
        return None
    runs = payload.get("runs", [])
    if not runs:
        print("No runs.")
        return None
    if _fmt(args) == "json":
        print(format_output(payload, fmt="json"))
        return None
    rows = [{k: r.get(k, "") for k in _LIST_COLUMNS} for r in runs]
    print(format_output(rows, fmt="table", columns=_LIST_COLUMNS))
    return None


def _get(args: argparse.Namespace, client: ForgeClient) -> Any:
    try:
        run = client.get_run(args.run_id)
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    except ForgeClientError as exc:
        print(f"Error: failed to fetch run: {exc}", file=sys.stderr)
        return None
    print(format_output(run, fmt="json"))
    return None


def _approval(args: argparse.Namespace, client: ForgeClient, decision: str) -> Any:
    try:
        run = client.submit_run_approval(args.run_id, decision)
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    except ForgeClientError as exc:
        # 409 (run not awaiting approval) surfaces here.
        print(f"Error: could not {decision} run: {exc}", file=sys.stderr)
        return None
    print(f"Run {run['id']} -> {run['status']}")
    return None


def _watch(args: argparse.Namespace, client: ForgeClient) -> Any:
    print(f"Watching run {args.run_id} (Ctrl-C to stop)...")
    try:
        for event in client.stream_run_events(args.run_id):
            status = str(event.get("status", ""))
            detail = event.get("error") or event.get("approval_reason") or ""
            print(f"  {status:<18} {detail}")
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except ForgeClientError as exc:
        print(f"Error: stream failed: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nStopped.")
    return None
