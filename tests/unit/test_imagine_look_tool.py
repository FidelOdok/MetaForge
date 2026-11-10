"""``twin.imagine_look``: the agent's eyes on an authored form (spec 23.6).

An agent that writes geometry code has to see the result and see it again after a
change. This tool renders the stored studio's engine page in headless Chromium inside the
process, moves a parameter if asked, and returns what each part drew plus the screenshots
as images. It reads and writes nothing in the twin, so a person is never asked to approve
it. Without Playwright and Chromium it says so by name and does not guess.
"""

from __future__ import annotations

import base64
import copy
from collections.abc import Iterator
from typing import Any

import pytest

from api_gateway.imagine import look
from api_gateway.imagine import routes as imagine_routes
from api_gateway.imagine.service import ImagineService
from api_gateway.operations import publisher as ops
from tests.unit.imagine_fixtures import authored_lamp_form, full_config
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def publisher() -> Iterator[Any]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture
def service() -> Iterator[ImagineService]:
    svc = ImagineService(InMemoryTwinAPI.create())
    imagine_routes.set_service(svc)
    yield svc
    imagine_routes.set_service(None)


def server(service: ImagineService) -> Any:
    from tool_registry.tools.twin.adapter import TwinServer

    return TwinServer(twin=service.twin, imagine_studio=service)


def lamp_config(form: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(full_config())
    cfg["form"] = form if form is not None else authored_lamp_form()
    return cfg


def _chromium_missing() -> str | None:
    if not look.playwright_available():
        return "playwright is not installed"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:
        return f"no Chromium build for Playwright: {type(exc).__name__}"
    return None


# -- registration: read only, in Imagine's own set, and told how to be used ---------------


async def test_the_tool_is_registered_as_a_read_in_the_imagine_stage(
    service: ImagineService,
) -> None:
    from mcp_core.annotations import annotations_for
    from mcp_core.profiles import CREATE_STAGE_TOOLS

    srv = server(service)
    assert "twin.imagine_look" in srv.tool_ids
    assert "twin.imagine_look" in CREATE_STAGE_TOOLS["imagine"]
    for other in ("develop", "check", "make"):
        assert "twin.imagine_look" not in CREATE_STAGE_TOOLS.get(other, set()), other
    assert annotations_for("twin.imagine_look").get("readOnlyHint") is True


async def test_the_description_tells_the_agent_to_name_the_revision_and_to_say_when_it_cannot_look(
    service: ImagineService,
) -> None:
    tool = server(service)._tools["twin.imagine_look"].manifest
    props = tool.input_schema["properties"]
    assert props["revision"]["type"] == "integer"
    assert props["set"]["type"] == "object"
    text = tool.description
    assert "revision" in text and "not run again" in text
    assert "could not look" in text
    assert "unavailable" in text


# -- the answers that need no browser ------------------------------------------------------


async def test_without_playwright_it_says_so_by_name_and_how_to_install_it(
    service: ImagineService, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    monkeypatch.setattr(look, "playwright_available", lambda: False)
    result = await srv.imagine_look({"project_id": PROJECT})
    assert result["status"] == "unavailable"
    assert "playwright install chromium" in result["install"]
    assert "images" not in result and "parts" not in result
    assert "could not look" in result["message"]


async def test_an_enclosure_studio_cannot_be_written_any_more(service: ImagineService) -> None:
    srv = server(service)
    legacy = lamp_config()
    legacy["form"] = {"template": "enclosure"}
    with pytest.raises(ValueError, match="enclosure"):
        await srv.imagine_put_studio({"project_id": PROJECT, "config": legacy})


async def test_a_legacy_enclosure_studio_is_answered_without_starting_a_browser(
    service: ImagineService, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv = server(service)
    legacy = lamp_config()
    legacy["form"] = {"template": "enclosure"}
    state = {
        "config": legacy,
        "status": "draft",
        "collections": {},
        "events": {},
        "handover": None,
        "rework": None,
        "warnings": [],
    }
    await service._write_revision(PROJECT, state, actor="test", reason="a studio from before")

    async def boom(*a: Any, **k: Any) -> Any:  # a browser must not start
        raise AssertionError("a browser was started for an enclosure studio")

    monkeypatch.setattr(look, "look_html", boom)
    result = await srv.imagine_look({"project_id": PROJECT})
    assert result["status"] == "not_authored"
    assert result["form"] == "enclosure"
    assert "removed" in result["message"] and "authored" in result["message"]


async def test_no_studio_is_an_error_not_an_empty_look(service: ImagineService) -> None:
    with pytest.raises(ValueError, match="twin.imagine_look"):
        await server(service).imagine_look({"project_id": PROJECT})


async def test_a_revision_that_is_not_the_head_is_refused(service: ImagineService) -> None:
    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    with pytest.raises(ValueError, match="revision 1"):
        await srv.imagine_look({"project_id": PROJECT, "revision": 99})


async def test_a_bad_set_argument_is_refused_with_a_reason(service: ImagineService) -> None:
    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    with pytest.raises(ValueError, match="set"):
        await srv.imagine_look({"project_id": PROJECT, "set": {"arm len": 3}})
    with pytest.raises(ValueError, match="set"):
        await srv.imagine_look({"project_id": PROJECT, "set": {"arm_len": "tall"}})


# -- a real browser, a real page -------------------------------------------------------------


@pytest.fixture(scope="module")
def chromium() -> None:
    reason = _chromium_missing()
    if reason:
        pytest.skip(reason)


async def test_it_looks_moves_a_parameter_looks_again_and_returns_the_pictures(
    service: ImagineService, chromium: None
) -> None:
    srv = server(service)
    put = await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    result = await srv.imagine_look(
        {"project_id": PROJECT, "revision": put["revision"], "set": {"arm_len": 400}}
    )
    assert result["status"] == "ready" and result["revision"] == put["revision"]
    assert {p["id"] for p in result["parts"]} == {"base", "arm", "shade", "panel", "button", "usbc"}
    assert result["size"][2] == pytest.approx(12 + 260 + 70, abs=0.5)
    after = result["after"]
    assert after["size"][2] == pytest.approx(12 + 400 + 70, abs=0.5)
    assert result["compare"]["params_changed"] == {"arm_len": [260, 400]}
    assert "arm" in result["compare"]["parts_changed"]
    assert result["requests_after_change"]["model_calls"] == []
    assert result["requests_after_change"]["outside"] == []
    assert [i["view"] for i in result["images"]] == ["first look", "after the change"]
    for image in result["images"]:
        assert base64.b64decode(image["png_base64"]).startswith(PNG_MAGIC)


async def test_the_pictures_become_image_blocks_for_the_client(
    service: ImagineService, chromium: None
) -> None:
    from metaforge.mcp.server import split_image_content

    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    result = await srv.imagine_look({"project_id": PROJECT, "set": {"arm_len": 300}})
    lean, blocks = split_image_content(result)
    assert len(blocks) == 2 and all(b["type"] == "image" for b in blocks)
    assert all("png_base64" not in i for i in lean["images"])
    assert [i["content_index"] for i in lean["images"]] == [1, 2]


async def test_looking_never_writes_to_the_twin(service: ImagineService, chromium: None) -> None:
    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    before = await srv.imagine_get_studio({"project_id": PROJECT})
    await srv.imagine_look({"project_id": PROJECT, "set": {"arm_len": 300}})
    after = await srv.imagine_get_studio({"project_id": PROJECT})
    assert after["revision"] == before["revision"] == 1
    assert after["config"]["form"]["params"]["arm_len"] == 260, "the move was only in the page"


async def test_a_part_that_fails_is_named_and_the_picture_of_the_rest_still_comes(
    service: ImagineService, chromium: None
) -> None:
    form = authored_lamp_form()
    form["authored"]["parts"][1]["code"] = "throw new Error('the arm broke');"
    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config(form)})
    result = await srv.imagine_look({"project_id": PROJECT})
    assert result["status"] == "error"
    assert any("the arm broke" in e for e in result["errors"])
    assert len(result["images"]) == 1
    drawn = {p["id"] for p in result["parts"] if p["error"] is None}
    assert "base" in drawn and "arm" not in drawn


async def test_the_page_under_the_gateways_own_policy_makes_no_request_that_leaves_it(
    service: ImagineService, chromium: None
) -> None:
    srv = server(service)
    await srv.imagine_put_studio({"project_id": PROJECT, "config": lamp_config()})
    result = await srv.imagine_look({"project_id": PROJECT})
    assert result["requests"]["outside"] == [] and result["requests"]["model_calls"] == []
    assert result["csp"].startswith("default-src 'none'"), "served with the gateway's policy"


# -- the waiting, without a browser: a page that is not ready yet --------------------------


class _FakeView:
    async def count(self) -> int:
        return 0


class _FakePage:
    """A page that publishes its form only after a few polls, as a slow one does."""

    def __init__(self, ready_after: int) -> None:
        self.ready_after = ready_after
        self.polls = 0

    def locator(self, selector: str) -> _FakeView:
        return _FakeView()

    async def screenshot(self) -> bytes:
        return PNG_MAGIC + b"fake"

    async def wait_for_timeout(self, ms: int) -> None:
        return None

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if js == look._DONE:
            self.polls += 1
            return self.polls >= self.ready_after
        if "FG_AUTHORED || null" in js:
            if self.polls < self.ready_after:
                return {"status": "running", "parts": [], "params": {}, "bbox": None}
            box = {"min": [0, 0, 0], "max": [10, 20, 30]}
            return {"status": "ready", "parts": [], "params": {}, "bbox": box, "triangles": 1}
        raise AssertionError(js)


async def test_it_waits_until_the_form_is_published_before_it_reads_it() -> None:
    page = _FakePage(ready_after=4)
    result = await look.observe(page, [], look.ORIGIN, None, timeout_s=5)
    assert result["before"]["status"] == "ready" and result["before"]["size"] == [10, 30, 20]
    assert page.polls >= 4


async def test_a_page_that_never_finishes_is_read_as_it_is_not_waited_on_forever() -> None:
    page = _FakePage(ready_after=10**9)
    result = await look.observe(page, [], look.ORIGIN, None, timeout_s=0.2)
    assert result["before"]["status"] == "running" and result["before"]["size"] is None
