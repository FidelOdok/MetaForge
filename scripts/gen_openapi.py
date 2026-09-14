#!/usr/bin/env python3
"""Dump the MetaForge Gateway OpenAPI spec to docs/reference/openapi.json (MET-554).

The gateway's API reference on the docs site is rendered from this committed
spec, so it stays in lockstep with the code — regenerate it whenever gateway
routes or schemas change (the CLAUDE.md "update docs before merge" rule).

Usage:
    python scripts/gen_openapi.py            # write docs/reference/openapi.json
    python scripts/gen_openapi.py --check    # fail if the committed spec is stale

The docs CI does NOT run this (it only builds the static site); the committed
JSON is the build input. Run it locally before merging route/schema changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# `python scripts/gen_openapi.py` sets sys.path[0] to this script's own
# directory (scripts/), not the repo root -- so `import api_gateway` falls
# through to whatever else provides that name (e.g. a globally editable-
# installed `pip install -e .` pointing at a *different* checkout, such as
# the main repo when this script is actually run from a git worktree).
# That silently generates the spec from the wrong tree's code with no
# error. Prepending the repo root guarantees this checkout wins.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Silence gateway startup logging so importing the app doesn't spam output.
logging.disable(logging.CRITICAL)

from api_gateway.server import create_app  # noqa: E402  (import after logging.disable)

OUTPUT = REPO_ROOT / "docs" / "reference" / "openapi.json"


def build_spec() -> dict:
    """Generate the OpenAPI schema from the FastAPI app (no server start)."""
    app = create_app()
    return app.openapi()


def serialize(spec: dict) -> str:
    """Stable, diff-friendly JSON (sorted keys, trailing newline)."""
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed spec differs from freshly generated.",
    )
    args = parser.parse_args()

    fresh = serialize(build_spec())

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != fresh:
            print(
                f"ERROR: {OUTPUT.relative_to(OUTPUT.parents[2])} is stale. "
                "Run `python scripts/gen_openapi.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {OUTPUT.name} is up to date.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(fresh, encoding="utf-8")
    paths = len(json.loads(fresh).get("paths", {}))
    print(f"Wrote {OUTPUT} ({paths} paths).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
