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
from tests.unit.imagine_fixtures import authored_lamp_form, base_config
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
