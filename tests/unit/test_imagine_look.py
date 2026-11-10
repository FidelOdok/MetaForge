"""The look script: open an authored form's page, read what it drew, take a picture.

An agent that writes geometry code needs to see the result, and to see it again after
a change. ``scripts/imagine_look.py`` opens the engine page in a real browser, waits
for the form to draw, reads ``window.FG_AUTHORED`` (parts, sizes, errors) and takes a
screenshot. Given ``--set name=value`` it moves that parameter's slider and looks
again, which is how a parameter change is shown to regenerate with no model call.
"""

from __future__ import annotations

import copy
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from PIL import Image

from api_gateway.imagine import routes as imagine_routes
from api_gateway.imagine.service import ImagineService
from scripts import imagine_look as look
from tests.unit.imagine_fixtures import authored_lamp_form, base_config, full_config
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"

SUMMARY: dict[str, Any] = {
    "status": "ready",
    "params": {"arm_len": 260},
    "bbox": {"min": [-55, 0, -55], "max": [55, 342, 55]},
    "triangles": 100,
    "errors": [],
    "parts": [
        {
            "id": "base",
            "name": "Base",
            "kind": "generated",
            "triangles": 60,
            "volume": 1.0,
            "error": None,
            "bbox": {"min": [-55, 0, -55], "max": [55, 12, 55]},
        },
        {
            "id": "arm",
            "name": "Arm",
            "kind": "generated",
            "triangles": 40,
            "volume": 1.0,
            "error": None,
            "bbox": {"min": [-4, 12, -4], "max": [4, 272, 4]},
        },
    ],
}


# -- the pure helpers --------------------------------------------------------------


def test_set_arguments_become_numbers() -> None:
    assert look.parse_sets(["arm_len=400", "head_r=45.5"]) == {"arm_len": 400.0, "head_r": 45.5}
    assert look.parse_sets([]) == {}


@pytest.mark.parametrize("bad", ["arm_len", "=3", "arm_len=abc", "a b=3", "arm_len=nan"])
def test_a_bad_set_argument_is_refused_with_a_reason(bad: str) -> None:
    with pytest.raises(ValueError, match="--set"):
        look.parse_sets([bad])


def test_a_summary_is_reduced_to_sizes_a_reader_can_use() -> None:
    s = look.summarize(SUMMARY)
    assert s["status"] == "ready" and s["size"] == [110, 110, 342]
    assert s["parts"][1] == {
        "id": "arm",
        "kind": "generated",
        "triangles": 40,
        "size": [8, 8, 260],
        "error": None,
    }
    assert look.summarize(None)["status"] == "none"


def test_a_failed_part_is_carried_into_the_summary() -> None:
    bad = copy.deepcopy(SUMMARY)
    bad["status"] = "error"
    bad["errors"] = ["Arm (arm): boom"]
    bad["parts"][1].update(error="boom", bbox=None, triangles=0)
    s = look.summarize(bad)
    assert s["errors"] == ["Arm (arm): boom"] and s["parts"][1]["error"] == "boom"
    assert s["parts"][1]["size"] is None


def test_comparing_two_looks_names_what_changed() -> None:
    after = copy.deepcopy(SUMMARY)
    after["params"] = {"arm_len": 400}
    after["bbox"]["max"][1] = 482
    after["parts"][1]["bbox"]["max"][1] = 412
    c = look.compare(look.summarize(SUMMARY), look.summarize(after))
    assert c["params_changed"] == {"arm_len": [260, 400]}
    assert c["size_delta"] == [0, 0, 140]
    assert c["parts_changed"] == ["arm"]


def test_the_pixel_difference_is_the_share_of_pixels_that_changed(tmp_path: Path) -> None:
    a, b, c = (tmp_path / n for n in ("a.png", "b.png", "c.png"))
    Image.new("RGB", (10, 10), (255, 255, 255)).save(a)
    same = Image.new("RGB", (10, 10), (255, 255, 255))
    same.save(b)
    diff = Image.new("RGB", (10, 10), (255, 255, 255))
    for x in range(5):
        for y in range(10):
            diff.putpixel((x, y), (0, 0, 0))
    diff.save(c)
    assert look.pixel_difference(a, b) == 0.0
    assert look.pixel_difference(a, c) == pytest.approx(0.5)


def test_requests_are_sorted_into_model_calls_and_outside_the_page() -> None:
    origin = "http://127.0.0.1:9999"
    urls = [
        f"{origin}/v1/twin/projects/x/imagine/engine/assets/engine.js",
        f"{origin}/v1/chat/completions",
        f"{origin}/v1/chat/send",
        "https://api.anthropic.com/v1/messages",
        "https://cdn.example.com/lib.js",
        "blob:http://127.0.0.1:9999/abc",
        "data:image/png;base64,AAA",
    ]
    out = look.classify_requests(urls, origin)
    assert out["model_calls"] == [
        f"{origin}/v1/chat/completions",
        f"{origin}/v1/chat/send",
        "https://api.anthropic.com/v1/messages",
    ]
    assert out["outside"] == [
        "https://api.anthropic.com/v1/messages",
        "https://cdn.example.com/lib.js",
    ]
    assert out["blobs"] == 1, "a blob: request is the page starting a sandbox worker"
    assert out["paths"] == {
        "/v1/twin/projects/x/imagine/engine/assets/engine.js": 1,
        "/v1/chat/completions": 1,
        "/v1/chat/send": 1,
        "https://api.anthropic.com/v1/messages": 1,
        "https://cdn.example.com/lib.js": 1,
    }


# -- a real browser, a real page ----------------------------------------------------


def _playwright_ready() -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
    except Exception as exc:  # no package, or no browser build
        return f"a Chromium browser is not available to Playwright: {type(exc).__name__}"
    return None


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    reason = _playwright_ready()
    if reason:
        pytest.skip(reason)
    twin = InMemoryTwinAPI.create()
    svc = ImagineService(twin)
    imagine_routes.set_service(svc)
    imagine_routes.get_service = lambda: svc  # type: ignore[assignment]
    app = FastAPI()
    app.include_router(imagine_routes.router)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/openapi.json", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield base
    srv.should_exit = True
    thread.join(timeout=10)
    imagine_routes.set_service(None)


def _put(base: str, project: str, form: dict[str, Any]) -> str:
    cfg = copy.deepcopy(base_config())
    cfg["form"] = form
    res = httpx.put(
        f"{base}/v1/twin/projects/{project}/imagine/studio", json={"config": cfg}, timeout=30
    )
    assert res.status_code == 200, res.text
    return f"{base}/v1/twin/projects/{project}/imagine/engine"


def test_the_lamp_is_drawn_and_a_parameter_change_redraws_it_with_no_model_call(
    server: str, tmp_path: Path
) -> None:
    url = _put(server, PROJECT, authored_lamp_form())
    result = look.look(url, tmp_path, sets={"arm_len": 400})
    before, after = result["before"], result["after"]
    assert before["status"] == "ready", before
    assert {p["id"] for p in before["parts"]} == {"base", "arm", "shade", "panel", "button", "usbc"}
    assert {p["kind"] for p in before["parts"]} == {"generated", "primitive", "component"}
    assert all(p["error"] is None and p["triangles"] > 0 for p in before["parts"])
    assert before["size"][2] == pytest.approx(12 + 260 + 70, abs=0.5)
    assert after["size"][2] == pytest.approx(12 + 400 + 70, abs=0.5)
    assert result["compare"]["params_changed"] == {"arm_len": [260, 400]}
    assert "arm" in result["compare"]["parts_changed"]
    assert result["pixel_difference"] > 0.005, "the picture did not change"
    assert result["requests_after_change"]["model_calls"] == []
    assert result["requests_after_change"]["outside"] == []
    assert Path(result["screenshots"][0]).stat().st_size > 5000
    assert Path(result["screenshots"][1]).stat().st_size > 5000


def test_nothing_leaves_the_page_while_it_loads(server: str, tmp_path: Path) -> None:
    url = _put(server, PROJECT, authored_lamp_form())
    result = look.look(url, tmp_path)
    assert result["requests"]["outside"] == [], "IMG-FORM-004: no outbound request from the page"
    assert result["requests"]["model_calls"] == []


def test_a_part_that_fails_is_reported_by_name_and_the_exit_code_says_so(
    server: str, tmp_path: Path
) -> None:
    form = authored_lamp_form()
    form["authored"]["parts"][1]["code"] = "throw new Error('the arm broke');"
    url = _put(server, "6e302521-bbe7-487f-a763-c73313f3e0f0", form)
    result = look.look(url, tmp_path)
    assert result["before"]["status"] == "error"
    assert any("the arm broke" in e for e in result["before"]["errors"])
    assert look.exit_code(result) == 2
    assert look.exit_code({"before": {"status": "ready"}, "after": None}) == 0


def test_a_page_that_is_not_an_authored_form_is_reported_not_waited_on_forever(
    server: str, tmp_path: Path
) -> None:
    url = _put(server, "5a1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c99", base_config()["form"])
    result = look.look(url, tmp_path, timeout_s=6)
    assert result["before"]["status"] == "none"
    assert look.exit_code(result) == 3


# -- what a person sees in the authored studio --------------------------------------------

_STUDIO_FACTS = """() => {
  const vis = (id) => {
    const e = document.getElementById(id);
    return !!e && getComputedStyle(e).display !== 'none' && !e.hidden;
  };
  const text = (sel) =>
    Array.from(document.querySelectorAll(sel)).map((e) => e.textContent.trim());
  const rows = Array.from(document.querySelectorAll('#quick .qs input.rng'));
  const flags = (ids) => Object.fromEntries(ids.map((i) => [i, vis(i)]));
  return {
    quick_sections: document.querySelectorAll('#quick details.pp').length,
    sliders: rows.map((r) => r.getAttribute('data-param') || r.getAttribute('aria-label')),
    slider_classes: Array.from(new Set(rows.map((r) => r.className))),
    intro: (document.querySelector('#quick .qintro') || {}).textContent || '',
    parts_listed: document.querySelectorAll('#quick [data-part]').length,
    graph_editor_visible: vis('canvas'),
    visible: flags(['gateBlk', 'reqBlk', 'optBlk', 'ctxBlk', 'mdlBlk', 'compBlk']),
    hidden: flags(['valBlk', 'dfaBlk', 'envBlk', 'verdictBlk', 'testBlk', 'elecBlk',
      'rulesBlk', 'bomBlk', 'partBlk', 'costBlk', 'handBlk', 'partReady']),
    req_chips: text('#reqList .chip'),
    assumptions_box: vis('asmBox') ? document.getElementById('asmBox').textContent : '',
    gate_message: (document.getElementById('gateMsg') || {}).textContent || '',
  };
}"""


def test_the_authored_studio_keeps_the_persons_controls_and_claims_nothing_about_a_placeholder(
    server: str,
) -> None:
    from playwright.sync_api import sync_playwright

    from api_gateway.imagine.look import CHROMIUM_ARGS

    cfg = copy.deepcopy(full_config())  # options, parts and assumptions, as a real studio has
    cfg["form"] = authored_lamp_form()
    res = httpx.put(
        f"{server}/v1/twin/projects/{PROJECT}/imagine/studio", json={"config": cfg}, timeout=30
    )
    assert res.status_code == 200, res.text
    url = f"{server}/v1/twin/projects/{PROJECT}/imagine/engine"
    assumptions = cfg["programme"]["assumptions"]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=list(CHROMIUM_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1200})
            page.goto(url, wait_until="domcontentloaded")
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if page.evaluate(
                    "() => !!(window.FG_AUTHORED && window.FG_AUTHORED.status === 'ready')"
                ):
                    break
                page.wait_for_timeout(200)
            page.wait_for_timeout(1500)  # the engine's own renderers run after the form is drawn
            facts = page.evaluate(_STUDIO_FACTS)
            # A person can act on the blockers the dashboard names: open Requirements, confirm
            # one assumption, and the gate counts down.
            gate = "() => document.getElementById('gateMsg').textContent"
            gate_before = page.evaluate(gate)
            page.locator('#dfm > section[data-acc="Requirements"] button.acc-h').click()
            page.locator("#asmBox input[type=checkbox]").first.check()
            page.wait_for_timeout(1500)
            gate_after = page.evaluate(gate)
            # The Programme tab needs the engine to have a shape; it must not say "connect a form".
            page.locator('button[data-pview="prog"]').click()
            page.wait_for_timeout(1500)
            programme = page.evaluate("() => document.getElementById('progView').textContent")
        finally:
            browser.close()

    # The controls look and behave like every other studio: the engine's own panel and slider.
    assert facts["quick_sections"] >= 2 and facts["slider_classes"] == ["rng"], facts
    assert sorted(facts["sliders"]) == sorted(authored_lamp_form()["params"]), facts["sliders"]
    assert facts["intro"].startswith("This form is built from code")
    assert facts["parts_listed"] == 6 and facts["graph_editor_visible"] is False
    # What carries the person's controls and records is there.
    assert all(facts["visible"].values()), facts["visible"]
    if assumptions:
        assert "Confirm these assumptions" in facts["assumptions_box"], facts["assumptions_box"]
    assert (
        "assumption" in facts["gate_message"].lower() or "intent" in facts["gate_message"].lower()
    )
    # What would assert something about the placeholder enclosure is not.
    assert not any(facts["hidden"].values()), facts["hidden"]
    # No requirement is called met.
    assert facts["req_chips"], "no requirement rows"
    assert set(facts["req_chips"]) <= {"Not assessed", "Off", "n/a"}, facts["req_chips"]
    count = len(assumptions)
    assert f"{count} assumption" in gate_before and f"{count - 1} assumption" in gate_after, (
        gate_before,
        gate_after,
    )
    # The Programme tab still works.
    assert "Connect a form" not in programme and "Phase exit" in programme, programme[:200]


_GRAPH_FACTS = """() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const box = (s) => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) }; };
  return {
    graph: box('#authGraph'), quick: box('#quick'), view: box('#viewPane'), right: box('#dfm'),
    nodes: q('#authGraph .agx-node').length, wires: q('#authGraph path.wire').length,
    sel: q('#authGraph .agx-node.sel').length, hl: q('#authGraph path.wire.hl').length,
    hideL: document.querySelector('main').classList.contains('hideL'),
    hideR: document.querySelector('main').classList.contains('hideR'),
  };
}"""


def _wait_ready(page: Any) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if page.evaluate("() => !!(window.FG_AUTHORED && window.FG_AUTHORED.status === 'ready')"):
            break
        page.wait_for_timeout(200)
    page.wait_for_timeout(1200)


def _engineer_graph_checks(page: Any, root: Any, form: dict[str, Any]) -> None:
    """Shared by the standalone and the hosted test. `root` is the page or the iframe's frame."""
    facts = root.evaluate(_GRAPH_FACTS)
    # Simple level keeps the engine's own panel and no graph.
    assert facts["graph"]["w"] == 0 and facts["quick"]["w"] > 0, facts
    root.locator('button[data-level="eng"]').click()
    page.wait_for_timeout(800)
    facts = root.evaluate(_GRAPH_FACTS)
    parts, params = form["authored"]["parts"], form["params"]
    uses = sum(len(p.get("uses", [])) for p in parts)
    # One node per parameter, per part and the form; one wire per use and per part into the form.
    assert facts["nodes"] == len(params) + len(parts) + 1, facts
    assert facts["wires"] == uses + len(parts), facts
    assert facts["graph"]["w"] > 200 and facts["quick"]["w"] == 0, facts
    # Three panes: the graph left of the model, the model left of the summary.
    assert facts["graph"]["w"] > 0 and facts["view"]["w"] > 200 and facts["right"]["w"] > 200

    # A slider in the graph moves the part's size and the Simple panel stays in step.
    key = next(iter(form["ranges"]))
    graph_slider = root.locator("#authGraph input[type=range]").first
    lo, hi = form["ranges"][key][0], form["ranges"][key][1]
    target = (lo + hi) / 2
    before = root.evaluate("() => JSON.stringify(window.FG_AUTHORED)")
    graph_slider.evaluate(
        "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }",
        target,
    )
    page.wait_for_timeout(1500)
    after = root.evaluate("() => JSON.stringify(window.FG_AUTHORED)")
    assert before != after, "moving a graph slider did not redraw the form"

    # Selecting a part lights its node and the wires into it, and clears on a background click.
    root.locator("#authGraph .agx-node .nh").nth(len(params)).click()
    page.wait_for_timeout(300)
    facts = root.evaluate(_GRAPH_FACTS)
    assert facts["sel"] == 1 and facts["hl"] >= 1, facts

    # Each side can be minimised and restored, and the model view takes the room.
    for edge, flag, side in (("#edgeL", "hideL", "graph"), ("#edgeR", "hideR", "right")):
        width = _settled(page, root)["view"]["w"]
        root.locator(edge).click()
        hidden = _settled(page, root)
        assert hidden[flag] is True and hidden["view"]["w"] > width, (edge, hidden)
        assert hidden[side]["w"] < 60, (edge, hidden)
        root.locator(edge).click()
        restored = _settled(page, root)
        assert restored[flag] is False and abs(restored["view"]["w"] - width) < 4, restored


def _settled(page: Any, root: Any) -> dict[str, Any]:
    """The layout facts once the grid's 0.2 s transition has stopped moving."""
    last: dict[str, Any] = {}
    for _ in range(40):
        page.wait_for_timeout(150)
        facts = root.evaluate(_GRAPH_FACTS)
        if last and facts == last:
            return facts
        last = facts
    return last


def test_the_engineer_level_draws_the_graph_and_both_sides_minimise(server: str) -> None:
    from playwright.sync_api import sync_playwright

    from api_gateway.imagine.look import CHROMIUM_ARGS

    form = authored_lamp_form()
    url = _put(server, PROJECT, form)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=list(CHROMIUM_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.goto(url, wait_until="domcontentloaded")
            _wait_ready(page)
            _engineer_graph_checks(page, page, form)
        finally:
            browser.close()


def test_the_graph_and_the_side_buttons_also_work_inside_the_dashboards_frame(
    server: str,
) -> None:
    from playwright.sync_api import sync_playwright

    from api_gateway.imagine.look import CHROMIUM_ARGS

    form = authored_lamp_form()
    url = _put(server, PROJECT, form)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=list(CHROMIUM_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            # The engine allows framing by its own origin only, so host the frame there.
            page.goto(f"{server}/openapi.json")
            page.evaluate(
                """(u) => { const f = document.createElement('iframe'); f.src = u;
                   f.style.cssText = 'width:1450px;height:900px;border:0';
                   document.body.append(f); }""",
                url,
            )
            frame = None
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                frame = next((f for f in page.frames if f.url.startswith(url)), None)
                if frame and frame.evaluate(
                    "() => !!(window.FG_AUTHORED && window.FG_AUTHORED.status === 'ready')"
                ):
                    break
                page.wait_for_timeout(300)
            assert frame is not None
            page.wait_for_timeout(1200)
            assert frame.evaluate("() => document.body.classList.contains('fg-hosted')") or (
                frame.evaluate("() => document.documentElement.classList.contains('fg-hosted')")
            )
            _engineer_graph_checks(page, frame, form)
        finally:
            browser.close()


def test_the_form_summary_is_a_card_with_tiles_and_the_not_assessed_list_is_its_own_card(
    server: str,
) -> None:
    from playwright.sync_api import sync_playwright

    from api_gateway.imagine.look import CHROMIUM_ARGS

    url = _put(server, PROJECT, authored_lamp_form())
    with sync_playwright() as p:
        browser = p.chromium.launch(args=list(CHROMIUM_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.goto(url, wait_until="domcontentloaded")
            _wait_ready(page)
            facts = page.evaluate(
                """() => {
                  const s = (sel) => document.querySelector(sel);
                  const card = (id) => { const e = s(id); if (!e) return null;
                    const h = e.querySelector(':scope > .acc-h');
                    const b = e.querySelector(':scope > .acc-b');
                    return { open: e.classList.contains('open'), header: !!h,
                      title: h && h.querySelector('.acc-t').textContent,
                      bodyShown: !!b && getComputedStyle(b).display !== 'none',
                      border: getComputedStyle(e).borderTopWidth,
                      tiles: e.querySelectorAll('.fg-fact').length }; };
                  return { form: card('#authBlk'), na: card('#authNa'),
                    order: Array.from(document.querySelectorAll('#dfm > section'))
                      .slice(0, 3).map(e => e.id) };
                }"""
            )
            assert facts["form"]["header"] and facts["form"]["title"] == "Form drawn", facts
            assert facts["form"]["open"] and facts["form"]["bodyShown"], facts
            assert facts["form"]["border"] != "0px" and facts["form"]["tiles"] == 5, facts
            assert facts["na"]["title"] == "Not assessed", facts
            assert not facts["na"]["open"] and not facts["na"]["bodyShown"], facts
            assert facts["order"][:2] == ["authBlk", "authNa"], facts
            # Both cards open and close like the engine's own.
            page.locator("#authNa > .acc-h").click()
            page.locator("#authBlk > .acc-h").click()
            page.wait_for_timeout(200)
            after = page.evaluate(
                """() => {
                  const open = (id) => document.querySelector(id).classList.contains('open');
                  return { na: open('#authNa'), form: open('#authBlk') };
                }"""
            )
            assert after == {"na": True, "form": False}, after
        finally:
            browser.close()


def _model_brightness(page: Any) -> float:
    """Mean brightness of the pixels in the model view that are not the page background."""
    import io

    from PIL import Image

    png = page.locator("#graphPane canvas, #viewPane canvas").first.screenshot()
    img = Image.open(io.BytesIO(png)).convert("L")
    w, h = img.size
    box = img.crop((w // 4, h // 5, 3 * w // 4, 4 * h // 5))
    data = list(box.getdata())
    bg = max(set(data), key=data.count)  # the commonest value is the empty backdrop
    body = [v for v in data if abs(v - bg) > 6]
    assert len(body) > 500, "the model did not draw"
    return sum(body) / len(body)


def test_the_colour_swatches_recolour_an_authored_form(server: str) -> None:
    from playwright.sync_api import sync_playwright

    from api_gateway.imagine.look import CHROMIUM_ARGS

    url = _put(server, PROJECT, authored_lamp_form())
    with sync_playwright() as p:
        browser = p.chromium.launch(args=list(CHROMIUM_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.goto(url, wait_until="domcontentloaded")
            _wait_ready(page)
            graphite = _model_brightness(page)
            page.locator('#cmf button[data-c="arctic"]').click()
            page.wait_for_timeout(1200)
            white = _model_brightness(page)
            page.locator('#cmf button[data-c="signal"]').click()
            page.wait_for_timeout(1200)
            orange = _model_brightness(page)
            page.locator('#cmf button[data-c="graphite"]').click()
            page.wait_for_timeout(1200)
            back = _model_brightness(page)
        finally:
            browser.close()
    assert white > graphite + 40, (graphite, white)
    assert white > orange > graphite, (graphite, orange, white)
    assert abs(back - graphite) < 6, (graphite, back)


def _mean_rgb(png_path: str | Path) -> tuple[float, float, float]:
    from PIL import Image

    img = Image.open(png_path).convert("RGB")
    w, h = img.size
    px = list(img.crop((w // 4, h // 5, 3 * w // 4, 4 * h // 5)).getdata())
    bg = max(set(px), key=px.count)
    body = [p for p in px if sum(abs(a - b) for a, b in zip(p, bg, strict=True)) > 30]
    assert len(body) > 500, "the model did not draw"
    n = len(body)
    return (
        sum(p[0] for p in body) / n,
        sum(p[1] for p in body) / n,
        sum(p[2] for p in body) / n,
    )


def test_a_colour_the_agent_writes_into_the_config_is_what_the_look_picture_shows(
    server: str, tmp_path: Path
) -> None:
    def shot(colour: str | None) -> tuple[float, float, float]:
        cfg = copy.deepcopy(base_config())
        cfg["form"] = authored_lamp_form()
        if colour:
            cfg["color"] = colour
        res = httpx.put(
            f"{server}/v1/twin/projects/{PROJECT}/imagine/studio", json={"config": cfg}, timeout=30
        )
        assert res.status_code == 200, res.text
        url = f"{server}/v1/twin/projects/{PROJECT}/imagine/engine"
        out = tmp_path / (colour or "default")
        out.mkdir()
        result = look.look(url, out, sets={})
        return _mean_rgb(result["screenshots"][0])

    default, cobalt, signal = shot(None), shot("cobalt"), shot("signal")
    assert cobalt[2] > cobalt[0] + 25, ("cobalt should read blue", cobalt)
    assert signal[0] > signal[2] + 25, ("signal should read orange", signal)
    assert default[0] < 140 and default[2] < 140, ("the default is graphite", default)
    # A colour the checker does not know is refused with the list, not drawn grey.
    bad = copy.deepcopy(base_config())
    bad["form"] = authored_lamp_form()
    bad["color"] = "red"
    res = httpx.put(
        f"{server}/v1/twin/projects/{PROJECT}/imagine/studio", json={"config": bad}, timeout=30
    )
    assert res.status_code >= 400 and "graphite" in res.text, res.text[:200]


def test_a_linked_component_is_drawn_in_the_page_at_the_size_of_its_real_part(
    server: str, tmp_path: Path
) -> None:
    cfg = copy.deepcopy(full_config())  # its USB-C part, usbc-a, is 9.0 x 7.5 x 3.2 mm
    cfg["form"] = authored_lamp_form()
    cfg["form"]["authored"]["parts"].append(
        {
            "id": "usb",
            "name": "USB-C socket",
            "kind": "component",
            "component_ref": "usbc-a",
            "at": [0, 0, 0],
        }
    )
    res = httpx.put(
        f"{server}/v1/twin/projects/{PROJECT}/imagine/studio", json={"config": cfg}, timeout=30
    )
    assert res.status_code == 200, res.text
    assert any("usb" in w and "drawn from context part" in w for w in res.json()["warnings"])
    result = look.look(f"{server}/v1/twin/projects/{PROJECT}/imagine/engine", tmp_path, sets={})
    parts = {p["id"]: p for p in result["before"]["parts"]}
    assert parts["usb"]["error"] is None
    # The page reports width, depth, height: the part's own length, width and height.
    for got, want in zip(parts["usb"]["size"], [9.0, 7.5, 3.2], strict=True):
        assert got == pytest.approx(want, abs=0.01), parts["usb"]["size"]
