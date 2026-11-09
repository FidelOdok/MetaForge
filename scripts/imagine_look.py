#!/usr/bin/env python3
"""Look at an authored form: what it drew, and a picture of it.

An agent that writes geometry code has to see the result, and see it again after a
change. This opens an Imagine engine page in a real browser, waits for the form to
draw, reads ``window.FG_AUTHORED`` (each part, its size, its errors) and saves a
screenshot. With ``--set name=value`` it then moves that parameter's slider and looks
again, which shows a parameter change redrawing the form with no model call: the
script lists every request the page made after the change.

    python scripts/imagine_look.py URL --out DIR
    python scripts/imagine_look.py --gateway http://127.0.0.1:8003 --project ID --out DIR \\
        --set arm_len=400

The logic lives in ``api_gateway/imagine/look.py``, which the ``twin.imagine_look`` tool
shares; this is only the command line. It needs the ``playwright`` package and a Chromium
build (``playwright install chromium``).
Exit code 0 when the form is drawn, 2 when a part failed, 3 when the page published no
authored form within the timeout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from api_gateway.imagine.look import (  # noqa: F401  (re-exported: tests and callers use them)
    INSTALL,
    LookUnavailable,
    classify_requests,
    compare,
    look_url,
    parse_sets,
    pixel_difference,
    summarize,
)


def look(
    url: str,
    out_dir: str | Path,
    sets: dict[str, float] | None = None,
    timeout_s: float = 30.0,
    width: int = 1400,
    height: int = 900,
) -> dict[str, Any]:
    """Open ``url``, record what the form drew, optionally change parameters and look again."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"url": url, "before": None, "after": None, "screenshots": []}
    try:
        raw = asyncio.run(look_url(url, sets, timeout_s, width, height))
    except LookUnavailable as exc:
        result["error"] = f"{exc}. Install with: {INSTALL}"
    else:
        for i, png in enumerate(raw.pop("shots"), start=1):
            path = out / f"look-{i}.png"
            path.write_bytes(png)
            result["screenshots"].append(str(path))
        result.update(raw)
        if result.get("after") is not None:
            result["request_count_after_change"] = sum(
                result["requests_after_change"]["paths"].values()
            )
    (out / "look.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def exit_code(result: dict[str, Any]) -> int:
    statuses = [
        (result.get("before") or {}).get("status"),
        (result.get("after") or {}).get("status"),
    ]
    if "error" in statuses:
        return 2
    if statuses[0] != "ready":
        return 3
    return 0


def report(result: dict[str, Any]) -> str:
    """The look as plain text: what an agent reads."""
    lines: list[str] = []

    def block(title: str, s: dict[str, Any]) -> None:
        size = " x ".join(str(v) for v in s["size"]) if s["size"] else "unknown"
        lines.append(
            f"{title}: {s['status']}, {size} mm (width x depth x height), "
            f"{s['triangles']} triangles"
        )
        for part in s["parts"]:
            psize = " x ".join(str(v) for v in part["size"]) if part["size"] else "not drawn"
            note = f"  ERROR: {part['error']}" if part["error"] else ""
            lines.append(
                f"  {part['id']} [{part['kind']}] {psize} mm, {part['triangles']} triangles{note}"
            )
        for err in s["errors"]:
            lines.append(f"  problem: {err}")

    if result.get("before"):
        block("first look", result["before"])
    if result.get("after"):
        block("after the change", result["after"])
        c = result["compare"]
        lines.append(
            f"changed: parameters {c['params_changed']}, size delta {c['size_delta']}, "
            f"parts {c['parts_changed']}"
        )
        diff = result["pixel_difference"]
        picture = f"{diff:.1%} of pixels" if diff is not None else "an unmeasured share (no Pillow)"
        after = result["requests_after_change"]
        lines.append(
            f"picture changed in {picture}; "
            f"{after['blobs']} sandbox workers started (blob: urls, not network), "
            f"{sum(after['paths'].values())} network requests after the change, "
            f"{len(after['model_calls'])} to a model, "
            f"{len(after['outside'])} outside the page"
        )
        for path, count in after["paths"].items():
            lines.append(f"  request after the change: {count} x {path}")
    lines.extend(f"screenshot: {s}" for s in result["screenshots"])
    if result.get("error"):
        lines.append(f"browser error: {result['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Look at an authored Imagine form in a real browser.")
    ap.add_argument("url", nargs="?", help="the engine page, or use --gateway and --project")
    ap.add_argument("--gateway", help="for example http://127.0.0.1:8003")
    ap.add_argument("--project", help="the project id")
    ap.add_argument("--out", default="imagine-look", help="where the screenshots and look.json go")
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="move a parameter and look again",
    )
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)
    url = args.url
    if not url:
        if not (args.gateway and args.project):
            ap.error("give a URL, or --gateway and --project")
        url = f"{args.gateway.rstrip('/')}/v1/twin/projects/{args.project}/imagine/engine"
    try:
        result = look(url, args.out, sets=parse_sets(args.set), timeout_s=args.timeout)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(report(result))
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
