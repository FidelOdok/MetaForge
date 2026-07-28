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
    print("Error: unknown cad subcommand", file=sys.stderr)
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
