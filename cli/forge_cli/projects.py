"""`forge projects` command handlers (MET-10).

List, inspect, and delete projects via the gateway Projects API
(``/v1/projects``). Surfaced as a first-class command because cleaning up or
inspecting projects previously had no CLI path.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError, ForgeClientNotFound
from cli.forge_cli.formatters import format_output

_LIST_COLUMNS = ["id", "name", "status", "work_products"]


def _fmt(args: argparse.Namespace) -> str:
    return "json" if getattr(args, "json", False) else "table"


def handle_projects(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge projects <subcommand>`."""
    sub = args.projects_command
    if sub == "list":
        return _list(args, client)
    if sub == "get":
        return _get(args, client)
    if sub == "delete":
        return _delete(args, client)
    print("Error: unknown projects subcommand", file=sys.stderr)
    return None


def _row(p: dict[str, Any]) -> dict[str, Any]:
    wps = p.get("work_products")
    count = len(wps) if isinstance(wps, list) else p.get("work_product_count", 0)
    return {
        "id": p.get("id", ""),
        "name": p.get("name", ""),
        "status": p.get("status", ""),
        "work_products": count,
    }


def _list(args: argparse.Namespace, client: ForgeClient) -> Any:
    try:
        payload = client.list_projects()
    except ForgeClientError as exc:
        print(f"Error: failed to list projects: {exc}", file=sys.stderr)
        return None
    projects = payload.get("projects", [])
    if args.status:
        projects = [p for p in projects if p.get("status") == args.status]
    if not projects:
        print("No projects." if not args.status else f"No {args.status!r} projects.")
        return None
    if _fmt(args) == "json":
        print(format_output({"projects": projects}, fmt="json"))
        return None
    rows = [_row(p) for p in projects]
    print(format_output(rows, fmt="table", columns=_LIST_COLUMNS))
    return None


def _get(args: argparse.Namespace, client: ForgeClient) -> Any:
    try:
        project = client.get_project(args.project_id)
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    except ForgeClientError as exc:
        print(f"Error: failed to fetch project: {exc}", file=sys.stderr)
        return None
    print(format_output(project, fmt="json"))
    return None


def _delete(args: argparse.Namespace, client: ForgeClient) -> Any:
    if not args.yes:
        reply = input(f"Delete project {args.project_id}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return None
    try:
        client.delete_project(args.project_id)
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    except ForgeClientError as exc:
        print(f"Error: failed to delete project: {exc}", file=sys.stderr)
        return None
    print(f"Deleted project {args.project_id}")
    return None
