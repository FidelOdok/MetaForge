"""`forge cad` command handlers (MET-10).

Author complex, multi-part CAD assemblies from the CLI deterministically (no
LLM) via ``POST /v1/cad/assembly``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError


def handle_cad(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge cad <subcommand>`."""
    if args.cad_command == "build":
        return _build(args, client)
    if args.cad_command == "from-text":
        return _from_text(args, client)
    print("Error: unknown cad subcommand", file=sys.stderr)
    return None


def _print_spec_summary(spec: dict[str, Any], stream: Any = None) -> None:
    """Print a one-line-per-part summary of a compiled spec to *stream* (default stdout)."""
    stream = stream if stream is not None else sys.stdout
    parts = spec.get("parts") or []
    print(f"Compiled '{spec.get('name', '?')}' → {len(parts)} part(s):", file=stream)
    for p in parts:
        params = p.get("parameters") or {}
        dims = ", ".join(f"{k}={v:g}" for k, v in params.items())
        extras = []
        if p.get("holes"):
            extras.append(f"{len(p['holes'])} hole(s)")
        if p.get("fillet"):
            extras.append(f"fillet {p['fillet']:g}")
        if p.get("chamfer"):
            extras.append(f"chamfer {p['chamfer']:g}")
        suffix = f"  [{', '.join(extras)}]" if extras else ""
        print(f"  · {p.get('name', '?')} ({p.get('kind')}): {dims}{suffix}", file=stream)


def _from_text(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Compile a plain-English description into an assembly (and build it, unless --dry-run)."""
    body: dict[str, Any] = {"description": args.description}
    if args.name:
        body["name"] = args.name
    if getattr(args, "provider", None):
        body["provider"] = args.provider
    if getattr(args, "model", None):
        body["model"] = args.model

    # Dry run: compile + validate only, print the spec JSON for review/editing.
    if getattr(args, "dry_run", False):
        try:
            result = client.compile_assembly(body)
        except ForgeClientError as exc:
            print(f"Error: compile failed: {exc}", file=sys.stderr)
            return None
        # Human summary → stderr, so stdout is only the JSON spec and
        # `... --dry-run > spec.json` yields a clean, buildable file.
        spec = result.get("spec") or {}
        _print_spec_summary(spec, sys.stderr)
        for warn in result.get("errors") or []:
            print(f"  ! {warn}", file=sys.stderr)
        if not result.get("buildable", True):
            print("Not buildable as-is — adjust the spec, then `forge cad build`.", file=sys.stderr)
        else:
            print(
                "Buildable. Save this JSON and run `forge cad build <file>`, "
                "or drop --dry-run to build now.",
                file=sys.stderr,
            )
        print(json.dumps(spec, indent=2))
        return None

    if args.project_id:
        body["project_id"] = args.project_id
    try:
        result = client.create_assembly_from_text(body)
    except ForgeClientError as exc:
        print(f"Error: text→CAD failed: {exc}", file=sys.stderr)
        return None

    _print_spec_summary(result.get("spec") or {})
    print(f"Committed: node {result.get('node_id')} ({result.get('part_count')} parts)")
    print(f"  view: {result.get('model_url')}")
    return None


def _build(args: argparse.Namespace, client: ForgeClient) -> Any:
    try:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: could not read spec {args.spec!r}: {exc}", file=sys.stderr)
        return None
    if not isinstance(spec, dict) or "name" not in spec or "parts" not in spec:
        print("Error: spec must be a JSON object with 'name' and 'parts'.", file=sys.stderr)
        return None
    if args.project_id:
        spec["project_id"] = args.project_id

    try:
        result = client.create_assembly(spec)
    except ForgeClientError as exc:
        print(f"Error: assembly build failed: {exc}", file=sys.stderr)
        return None

    print(
        f"Committed assembly '{spec['name']}' "
        f"({result.get('part_count', len(spec['parts']))} parts): "
        f"node {result.get('node_id')}"
    )
    print(f"  view: {result.get('model_url')}")
    return None
