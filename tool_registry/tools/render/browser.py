"""Drive headless Chromium through Playwright to render a plan (the browser half).

The page and every file it loads are answered from local files by
``page.route``; any other request is aborted, so a render never reaches a
network. Playwright is imported only when a render runs: the gateway and
sidecar images do not ship it, and say so (:class:`BrowserNotAvailableError`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.tools.render.page import (
    ORIGIN,
    STATIC_DIR,
    build_page_html,
    content_type,
    resolve_route,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.render.browser")

#: WebGL without a GPU: Chromium's SwiftShader software renderer.
CHROMIUM_ARGS = (
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--disable-dev-shm-usage",
)


class BrowserNotAvailableError(RuntimeError):
    """Playwright or its Chromium is not installed where this adapter runs."""


class RenderPageError(RuntimeError):
    """The page could not render the model (bad GLB, WebGL failure, timeout)."""


def playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


async def render_in_browser(
    glb: bytes,
    plan: dict[str, Any],
    *,
    three_dir: Path,
    timeout_s: float,
    static_dir: Path = STATIC_DIR,
) -> dict[str, Any]:
    """Render ``plan`` of ``glb``; the page's raw result (data URLs, bbox, triangles)."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserNotAvailableError(
            "Playwright is not installed here; render.model_views runs in the "
            "render-adapter container (METAFORGE_ADAPTER_RENDER_URL)"
        ) from exc
    if not (three_dir / "build" / "three.module.js").is_file():
        raise BrowserNotAvailableError(f"three.js is not installed at {three_dir}")

    html = build_page_html().encode("utf-8")
    refused: list[str] = []

    async def handle(route: Any) -> None:
        url = str(route.request.url)
        if not url.startswith(ORIGIN + "/"):
            refused.append(url[:200])
            await route.abort()
            return
        path = url[len(ORIGIN) :]
        if path in ("/", "/index.html"):
            await route.fulfill(status=200, body=html, content_type="text/html")
            return
        if path.split("?", 1)[0] == "/model.glb":
            await route.fulfill(status=200, body=glb, content_type="model/gltf-binary")
            return
        target = resolve_route(path, three_dir=three_dir, static_dir=static_dir)
        if target is None or not target.is_file():
            refused.append(path[:200])
            await route.fulfill(status=404, body=b"not found")
            return
        await route.fulfill(status=200, body=target.read_bytes(), content_type=content_type(target))

    with tracer.start_as_current_span("render.browser") as span:
        span.set_attribute("render.views", len(plan.get("views") or []))
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
                try:
                    page = await browser.new_page(
                        viewport={"width": int(plan["width"]), "height": int(plan["height"])},
                        device_scale_factor=1,
                    )
                    errors: list[str] = []
                    page.on("pageerror", lambda exc: errors.append(str(exc)[:300]))
                    await page.route("**/*", handle)
                    await page.goto(f"{ORIGIN}/index.html", wait_until="load")
                    await page.wait_for_function("window.renderReady === true", timeout=20_000)
                    result = await asyncio.wait_for(
                        page.evaluate("(plan) => window.renderViews(plan)", plan), timeout_s
                    )
                    span.set_attribute("render.browser_version", browser.version)
                    if not isinstance(result, dict):
                        raise RenderPageError("the page returned no result")
                    result["browser"] = f"chromium {browser.version}"
                    if errors:
                        result["page_errors"] = errors[:5]
                    if refused:
                        result["refused_requests"] = refused[:10]
                    return result
                finally:
                    await browser.close()
        except TimeoutError as exc:
            span.record_exception(exc)
            raise RenderPageError(f"the render took longer than {timeout_s:g} s") from exc
        except PlaywrightError as exc:
            span.record_exception(exc)
            message = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
            if "Executable doesn't exist" in message:
                raise BrowserNotAvailableError(
                    "Chromium is not installed for Playwright here"
                ) from exc
            raise RenderPageError(f"the page could not render the model: {message}") from exc
