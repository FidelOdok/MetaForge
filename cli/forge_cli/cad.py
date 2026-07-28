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


def _from_text(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Compile a plain-English description into an assembly and build it."""
    body: dict[str, Any] = {"description": args.description}
    if args.name:
        body["name"] = args.name
    if args.project_id:
        body["project_id"] = args.project_id
    if getattr(args, "provider", None):
        body["provider"] = args.provider
    if getattr(args, "model", None):
        body["model"] = args.model

    try:
        result = client.create_assembly_from_text(body)
    except ForgeClientError as exc:
        print(f"Error: text→CAD failed: {exc}", file=sys.stderr)
        return None

    spec = result.get("spec") or {}
    parts = spec.get("parts") or []
    print(f"Compiled '{spec.get('name', '?')}' → {len(parts)} part(s):")
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
        print(f"  · {p.get('name', '?')} ({p.get('kind')}): {dims}{suffix}")
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
