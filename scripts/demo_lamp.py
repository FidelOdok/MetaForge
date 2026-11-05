#!/usr/bin/env python3
"""Run the desk lamp golden path against a gateway and record what it produced.

Step 1 of the golden path (the product definition and the interface
contracts). Against a running gateway:

1. seed the lamp project (``POST /v1/examples/desk-lamp/seed``; idempotent,
   never overwrites);
2. check every interface contract against the current revisions
   (``POST /v1/twin/projects/{id}/contracts/check``), which records one check
   per contract;
3. print the contract status table and the steps still missing;
4. write a JSON manifest to ``<project-dir>/.forge/demo_manifest.json`` with
   the seed outcome, each contract's status and check record id, every step as
   produced (with refs, links and run ids) or missing (with the step that
   would produce it), and the known limitations.

It never fabricates a result: what has not been produced is listed as missing.
Later steps of the golden path fill in CAD, EDA, firmware, simulation and
release, and the same runner reports them as they appear.

Usage::

    python scripts/demo_lamp.py --gateway http://localhost:8000 --project-dir desk-lamp
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

DEFAULT_GATEWAY = "http://localhost:8000"


def _json(response: Any) -> Any:
    response.raise_for_status()
    return response.json()


def run(client: Any, *, project_dir: Path, out: Any = None) -> dict[str, Any]:
    """Seed, check, write the manifest and print the table. ``client`` is httpx-like."""
    stream = out or sys.stdout
    run_id = f"demo-lamp-{uuid4()}"
    seed = _json(client.post("/v1/examples/desk-lamp/seed"))
    pid = seed["project_id"]
    checked = _json(client.post(f"/v1/twin/projects/{pid}/contracts/check", json={}))
    records = {c["contract_key"]: c["record_id"] for c in checked["checks"]}
    contracts = _json(client.get(f"/v1/twin/projects/{pid}/contracts"))["contracts"]
    manifest = _json(client.get(f"/v1/examples/desk-lamp/projects/{pid}/manifest"))
    manifest["run_id"] = run_id
    manifest["seed"] = seed
    manifest["check_records"] = records
    manifest["run_ids"] = sorted(set(manifest.get("run_ids") or []) | set(records.values()))

    target = project_dir / ".forge" / "demo_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Desk lamp golden path: project {seed['name']} ({pid})", file=stream)
    print(f"Run {run_id}", file=stream)
    print("", file=stream)
    print(
        f"{'Contract':<20} {'Version':<8} {'Status':<10} {'Check':<14} What it means", file=stream
    )
    for c in contracts:
        latest = c.get("latest_check") or {}
        outcome = latest.get("outcome") or "unchecked"
        if latest.get("stale"):
            outcome = "stale"
        print(
            f"{c['key']:<20} v{c['interface_version']:<7} {c['status']:<10} {outcome:<14} "
            f"{c['create_line']}",
            file=stream,
        )
    print("", file=stream)
    missing = manifest.get("missing") or []
    print(f"Missing ({len(missing)} steps, nothing fabricated):", file=stream)
    for m in missing:
        print(f"  - {m['label']}: produced by {m['produced_by']}", file=stream)
    print("", file=stream)
    print(f"Manifest: {target}", file=stream)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--gateway",
        default=os.environ.get("METAFORGE_GATEWAY_URL", DEFAULT_GATEWAY),
        help="Gateway base URL (default: $METAFORGE_GATEWAY_URL or http://localhost:8000)",
    )
    parser.add_argument(
        "--project-dir",
        default="desk-lamp",
        help="Where the manifest is written, under .forge/ (default: ./desk-lamp)",
    )
    parser.add_argument("--token", default=os.environ.get("METAFORGE_TOKEN"), help="Bearer token")
    args = parser.parse_args(argv)

    import httpx

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(base_url=args.gateway, headers=headers, timeout=120.0) as client:
        try:
            run(client, project_dir=Path(args.project_dir))
        except httpx.HTTPStatusError as exc:
            print(
                f"The gateway refused: {exc.response.status_code} {exc.response.text}",
                file=sys.stderr,
            )
            return 1
        except httpx.HTTPError as exc:
            print(f"Could not reach the gateway at {args.gateway}: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
