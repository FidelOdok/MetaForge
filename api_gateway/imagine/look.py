"""Look at an authored form: what it drew, and a picture of it (spec 23.6).

An agent that writes geometry code has to see the result, and see it again after a
change. This opens an Imagine engine page in headless Chromium, waits for the form to
draw, reads ``window.FG_AUTHORED`` (each part, its size, its errors) and takes a
screenshot. Given parameters to move it then moves each slider and looks again, which shows
a change redrawing the form with no model call: every request the page makes after the
change is listed.

Two ways to get a page, one way to look at it:

* :func:`look_html` serves the engine page and its assets to the browser from memory
  (``page.route``), with the gateway's own Content Security Policy, and aborts any other
  request. No gateway has to be running, so it works in the stdio plugin. This is what
  ``twin.imagine_look`` uses.
* :func:`look_url` opens a real URL. ``scripts/imagine_look.py`` uses it.

Playwright and Pillow are imported only when a look runs: a plain install does not ship
them, and a look without Playwright and a Chromium build says so by name
(:class:`LookUnavailable`) instead of guessing. Without Pillow only the picture comparison
is missing.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from api_gateway.imagine import engine_page
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.look")

#: The origin the page is served from when there is no gateway. Nothing is ever fetched from it.
ORIGIN = "http://imagine.metaforge.invalid"

#: WebGL without a GPU: Chromium's SwiftShader software renderer (as the render adapter does).
CHROMIUM_ARGS = (
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--disable-dev-shm-usage",
)

INSTALL = "pip install playwright pillow && playwright install chromium"

# A request to any of these is a call to a model. The page must make none of them.
_MODEL_PATH = re.compile(r"/(chat|completions|messages|llm|generate)(/|$|\?)", re.I)
_MODEL_HOST = re.compile(r"(anthropic|openai|openrouter)\.", re.I)
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")

_DONE = "() => !!(window.FG_AUTHORED && ['ready', 'error'].includes(window.FG_AUTHORED.status))"
_MOVED = (
    "({ want, tol }) => { const f = window.FG_AUTHORED; return !!(f && "
    "['ready', 'error'].includes(f.status) && "
    "Object.keys(want).every((k) => Math.abs(f.params[k] - want[k]) <= tol[k])); }"
)
_SET_VALUE = (
    "(el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })); }"
)


class LookUnavailable(RuntimeError):
    """Playwright, or a Chromium build for it, is not installed where this runs."""


def playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


# -- the pure helpers -------------------------------------------------------------------


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


def check_sets(value: Any) -> dict[str, float]:
    """The ``set`` argument of the tool: parameter names mapped to numbers."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(
            "'set' must map parameter names to numbers, for example {\"arm_len\": 400}"
        )
    out: dict[str, float] = {}
    for name, raw in value.items():
        ok = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw)
        if not isinstance(name, str) or not _PARAM_NAME.fullmatch(name) or not ok:
            raise ValueError(f"'set' needs a parameter name and a number (got {name!r}: {raw!r})")
        out[name] = float(raw)
    return out


def _clean(value: float) -> float | int:
    rounded = round(value, 1)
    return int(rounded) if rounded == int(rounded) else rounded


def _span(bbox: dict[str, list[float]] | None) -> list[float] | None:
    """Width, depth, height in mm: x, z, y of a bounding box (Y is up)."""
    if not bbox:
        return None
    lo, hi = bbox["min"], bbox["max"]
    return [_clean(hi[0] - lo[0]), _clean(hi[2] - lo[2]), _clean(hi[1] - lo[1])]


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


def pixel_difference(
    a: str | Path | bytes, b: str | Path | bytes, threshold: int = 16
) -> float | None:
    """The share of pixels that differ between two screenshots by more than ``threshold``.

    ``None`` when Pillow is not installed: the comparison is a convenience, not a fact the
    look depends on.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    import io

    def open_image(src: str | Path | bytes) -> Any:
        return Image.open(io.BytesIO(src) if isinstance(src, bytes) else src)

    with open_image(a) as ia, open_image(b) as ib:
        left, right = ia.convert("RGB"), ib.convert("RGB")
        if left.size != right.size:
            right = right.resize(left.size)
        diff = ImageChops.difference(left, right).convert("L")
        changed = sum(diff.histogram()[threshold + 1 :])
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


# -- looking, in a browser ----------------------------------------------------------------


async def _wait(page: Any, predicate: str, timeout_s: float, arg: Any = None) -> bool:
    """Poll a page predicate until it is true or the time is up.

    Not ``page.wait_for_function``: that evaluates its predicate as a string inside the page,
    and the engine page's Content Security Policy forbids that (``script-src 'self'``).
    ``page.evaluate`` goes through the debugger and is not affected.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if await page.evaluate(predicate, arg):
            return True
        if time.monotonic() >= deadline:
            return False
        await page.wait_for_timeout(150)


async def _shoot(page: Any) -> bytes:
    """The 3D view, or the whole page when the view cannot be found."""
    view = page.locator("#viewPane")
    shot: bytes = await (view.screenshot() if await view.count() else page.screenshot())
    return shot


async def observe(
    page: Any, seen: list[str], origin: str, sets: Mapping[str, float] | None, timeout_s: float
) -> dict[str, Any]:
    """Look at an open engine page, move ``sets`` if asked, and look again.

    ``seen`` is the list of request urls the caller is collecting from the page. Returns the
    summaries, the comparison, the request classification and the screenshots as bytes.
    """
    await _wait(page, _DONE, timeout_s)  # not an authored form, or never finished: "none"
    shots = [await _shoot(page)]
    before = summarize(await page.evaluate("window.FG_AUTHORED || null"))
    result: dict[str, Any] = {
        "before": before,
        "after": None,
        "shots": shots,
        "requests": classify_requests(list(seen), origin),
    }
    if sets and before["status"] != "none":
        mark = len(seen)
        tolerance: dict[str, float] = {}
        for name, value in sets.items():
            slider = page.locator(f'input[data-param="{name}"]')
            if await slider.count() == 0:
                known = ", ".join(sorted(before["params"]))
                raise ValueError(f'there is no parameter "{name}"; the form has: {known}')
            # A slider snaps to its own step, so ask for the value within half a step.
            tolerance[name] = float(await slider.get_attribute("step") or 0) / 2 + 1e-9
            await slider.evaluate(_SET_VALUE, value)
        await _wait(page, _MOVED, timeout_s, {"want": dict(sets), "tol": tolerance})
        shots.append(await _shoot(page))
        after = summarize(await page.evaluate("window.FG_AUTHORED || null"))
        result["after"] = after
        result["compare"] = compare(before, after)
        result["pixel_difference"] = pixel_difference(shots[0], shots[1])
        result["requests_after_change"] = classify_requests(list(seen[mark:]), origin)
    return result


async def _launch(playwright: Any) -> Any:
    try:
        return await playwright.chromium.launch(args=list(CHROMIUM_ARGS))
    except Exception as exc:  # Playwright's own error type is not importable without it
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise LookUnavailable(f"Chromium could not start for Playwright: {first}") from exc


def _require_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise LookUnavailable("the playwright package is not installed") from exc
    return async_playwright


async def look_url(
    url: str,
    sets: Mapping[str, float] | None = None,
    timeout_s: float = 30.0,
    width: int = 1400,
    height: int = 900,
) -> dict[str, Any]:
    """Open a real URL, record what the form drew, optionally move parameters and look again."""
    async_playwright = _require_playwright()
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    seen: list[str] = []
    async with async_playwright() as p:
        browser = await _launch(p)
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            page.on("request", lambda r: seen.append(r.url))
            # The engine solves on the page's own thread, so the load event can come late;
            # what we wait for is the authored form, in observe().
            await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
            return await observe(page, seen, origin, sets, timeout_s)
        finally:
            await browser.close()


async def look_html(
    html: str,
    project_id: str,
    sets: Mapping[str, float] | None = None,
    timeout_s: float = 40.0,
    width: int = 1280,
    height: int = 800,
) -> dict[str, Any]:
    """Look at the engine page ``html`` without a gateway.

    The page and its assets are answered from memory with the gateway's own headers, so the
    Content Security Policy under test is the real one; every other request is aborted.
    """
    async_playwright = _require_playwright()
    host = urlsplit(ORIGIN).netloc
    page_path = f"/v1/twin/projects/{project_id}/imagine/engine"
    assets_prefix = page_path + "/assets/"
    headers = engine_page.page_headers()
    seen: list[str] = []

    async def handle(route: Any) -> None:
        parts = urlsplit(route.request.url)
        if parts.netloc != host:
            await route.abort()
        elif parts.path == page_path:
            await route.fulfill(
                status=200,
                body=html,
                headers={**headers, "Content-Type": "text/html; charset=utf-8"},
            )
        elif (
            parts.path.startswith(assets_prefix)
            and parts.path[len(assets_prefix) :] in engine_page.ASSETS
        ):
            name = parts.path[len(assets_prefix) :]
            await route.fulfill(
                status=200,
                body=engine_page.asset_bytes(name),
                headers={
                    **engine_page.asset_headers(),
                    "Content-Type": engine_page.asset_media_type(name),
                },
            )
        else:
            await route.abort()

    with tracer.start_as_current_span("imagine.look") as span:
        span.set_attribute("project_id", project_id)
        async with async_playwright() as p:
            browser = await _launch(p)
            try:
                context = await browser.new_context(viewport={"width": width, "height": height})
                await context.route("**/*", handle)
                page = await context.new_page()
                page.on("request", lambda r: seen.append(r.url))
                await page.goto(
                    ORIGIN + page_path, wait_until="domcontentloaded", timeout=int(timeout_s * 1000)
                )
                result = await observe(page, seen, ORIGIN, sets, timeout_s)
            finally:
                await browser.close()
        result["csp"] = headers["Content-Security-Policy"]
        logger.info(
            "imagine_look",
            project_id=project_id,
            status=(result["after"] or result["before"])["status"],
            moved=sorted(sets or {}),
        )
        return result
