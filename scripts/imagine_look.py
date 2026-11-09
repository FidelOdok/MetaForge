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

Needs the ``playwright`` package and a Chromium build (``playwright install chromium``).
Exit code 0 when the form is drawn, 2 when a part failed, 3 when the page published no
authored form within the timeout.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# A request to any of these is a call to a model. The page must make none of them.
_MODEL_PATH = re.compile(r"/(chat|completions|messages|llm|generate)(/|$|\?)", re.I)
_MODEL_HOST = re.compile(r"(anthropic|openai|openrouter)\.", re.I)
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


def parse_sets(items: list[str]) -> dict[str, float]:
    """``["arm_len=400"]`` to ``{"arm_len": 400.0}``, refusing anything that is not a number."""
    out: dict[str, float] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if not sep or not _PARAM_NAME.fullmatch(name) or not math.isfinite(value):
            raise ValueError(f'--set needs name=number, for example arm_len=400 (got "{item}")')
        out[name] = value
    return out


def _span(bbox: dict[str, list[float]] | None) -> list[float] | None:
    """Width, depth, height in mm: x, z, y of a bounding box (Y is up)."""
    if not bbox:
        return None
    lo, hi = bbox["min"], bbox["max"]
    return [_clean(hi[0] - lo[0]), _clean(hi[2] - lo[2]), _clean(hi[1] - lo[1])]


def _clean(value: float) -> float | int:
    rounded = round(value, 1)
    return int(rounded) if rounded == int(rounded) else rounded


def summarize(fg: dict[str, Any] | None) -> dict[str, Any]:
    """The facts a reader needs from ``window.FG_AUTHORED``."""
    if not fg:
        return {
            "status": "none",
            "size": None,
            "triangles": 0,
            "parts": [],
            "errors": [],
            "params": {},
        }
    return {
        "status": fg.get("status", "none"),
        "size": _span(fg.get("bbox")),
        "triangles": fg.get("triangles", 0),
        "params": dict(fg.get("params") or {}),
        "errors": list(fg.get("errors") or []),
        "parts": [
            {
                "id": p["id"],
                "kind": p.get("kind"),
                "triangles": p.get("triangles", 0),
                "size": _span(p.get("bbox")),
                "error": p.get("error"),
            }
            for p in fg.get("parts") or []
        ],
    }


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed between two summaries."""
    keys = set(before["params"]) | set(after["params"])
    params = {
        k: [before["params"].get(k), after["params"].get(k)]
        for k in sorted(keys)
        if before["params"].get(k) != after["params"].get(k)
    }
    delta = (
        [_clean(a - b) for a, b in zip(after["size"], before["size"], strict=True)]
        if before["size"] and after["size"]
        else None
    )
    was = {p["id"]: p["size"] for p in before["parts"]}
    changed = [p["id"] for p in after["parts"] if was.get(p["id"]) != p["size"]]
    return {"params_changed": params, "size_delta": delta, "parts_changed": changed}


def pixel_difference(a: str | Path, b: str | Path, threshold: int = 16) -> float:
    """The share of pixels that differ between two screenshots by more than ``threshold``."""
    from PIL import Image, ImageChops

    with Image.open(a) as ia, Image.open(b) as ib:
        left, right = ia.convert("RGB"), ib.convert("RGB")
        if left.size != right.size:
            right = right.resize(left.size)
        diff = ImageChops.difference(left, right).convert("L")
        changed = sum(1 for v in diff.get_flattened_data() if v > threshold)
        return changed / (left.size[0] * left.size[1])


def classify_requests(urls: list[str], origin: str) -> dict[str, Any]:
    """Which requests were calls to a model, and which left the page's own origin.

    ``blob:`` and ``data:`` urls are the page talking to itself (each sandbox worker starts
    from a blob) and are not counted as outside; blobs are counted on their own.
    """
    host = urlsplit(origin).netloc
    model: list[str] = []
    outside: list[str] = []
    paths: dict[str, int] = {}
    blobs = 0
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme == "blob":
            blobs += 1
        if parts.scheme in ("blob", "data", "about"):
            continue
        key = parts.path if parts.netloc == host else url.split("?")[0]
        paths[key] = paths.get(key, 0) + 1
        if parts.netloc != host:
            outside.append(url)
        if (
            parts.netloc != host
            and _MODEL_HOST.search(parts.netloc)
            or _MODEL_PATH.search(parts.path)
        ):
            model.append(url)
    return {"model_calls": model, "outside": outside, "paths": paths, "blobs": blobs}


_DONE = "() => !!(window.FG_AUTHORED && ['ready', 'error'].includes(window.FG_AUTHORED.status))"


def _wait(page: Any, predicate: str, timeout_s: float, arg: Any = None) -> bool:
    """Poll a page predicate until it is true or the time is up.

    Not ``page.wait_for_function``: that evaluates its predicate as a string inside the page,
    and the engine page's Content Security Policy forbids that (``script-src 'self'``).
    ``page.evaluate`` goes through the debugger and is not affected.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if page.evaluate(predicate, arg):
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(150)


def look(
    url: str,
    out_dir: str | Path,
    sets: dict[str, float] | None = None,
    timeout_s: float = 30.0,
    width: int = 1400,
    height: int = 900,
) -> dict[str, Any]:
    """Open ``url``, record what the form drew, optionally change parameters and look again."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    seen: list[str] = []
    shots: list[str] = []
    result: dict[str, Any] = {"url": url, "before": None, "after": None, "screenshots": shots}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("request", lambda r: seen.append(r.url))
            # The engine solves on the page's own thread, so the load event can come late;
            # what we wait
            # for is the authored form, below.
            page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
            # Not an authored form, or it never finished: reported as status "none".
            _wait(page, _DONE, timeout_s)
            first = out / "look-1.png"
            page.screenshot(path=str(first))
            shots.append(str(first))
            result["before"] = summarize(page.evaluate("window.FG_AUTHORED || null"))
            result["requests"] = classify_requests(list(seen), origin)

            if sets and result["before"]["status"] != "none":
                mark = len(seen)
                tolerance: dict[str, float] = {}
                for name, value in sets.items():
                    slider = page.locator(f'input[data-param="{name}"]')
                    if slider.count() == 0:
                        known = sorted(result["before"]["params"])
                        raise ValueError(
                            f'there is no parameter "{name}"; the form has: {", ".join(known)}'
                        )
                    # A slider snaps to its own step, so ask for the value within half a step.
                    tolerance[name] = float(slider.get_attribute("step") or 0) / 2 + 1e-9
                    slider.evaluate(
                        "(el, v) => { el.value = String(v); "
                        "el.dispatchEvent(new Event('input', { bubbles: true })); }",
                        value,
                    )
                _wait(
                    page,
                    "({ want, tol }) => { const f = window.FG_AUTHORED; return !!(f && "
                    "['ready', 'error'].includes(f.status) && "
                    "Object.keys(want).every((k) => Math.abs(f.params[k] - want[k]) <= tol[k])); }",
                    timeout_s,
                    {"want": sets, "tol": tolerance},
                )
                second = out / "look-2.png"
                page.screenshot(path=str(second))
                shots.append(str(second))
                result["after"] = summarize(page.evaluate("window.FG_AUTHORED || null"))
                result["compare"] = compare(result["before"], result["after"])
                result["pixel_difference"] = pixel_difference(first, second)
                result["requests_after_change"] = classify_requests(list(seen[mark:]), origin)
                result["request_count_after_change"] = len(seen) - mark
        except PlaywrightError as exc:
            result["error"] = str(exc).splitlines()[0]
        finally:
            browser.close()

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
        lines.append(
            f"picture changed in {result['pixel_difference']:.1%} of pixels; "
            f"{result['requests_after_change']['blobs']} sandbox workers started "
            "(blob: urls, not network), "
            f"{sum(result['requests_after_change']['paths'].values())} network requests "
            "after the change, "
            f"{len(result['requests_after_change']['model_calls'])} to a model, "
            f"{len(result['requests_after_change']['outside'])} outside the page"
        )
        for path, count in result["requests_after_change"]["paths"].items():
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
