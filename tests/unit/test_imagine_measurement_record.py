"""A look's measurements are recorded against the revision, on the server (plan C10b).

The measurements come from the headless browser the gateway itself runs for
``twin.imagine_look``, never from a person's browser: no route accepts one. They belong to
one revision, so a newer write drops them rather than carrying a stale figure forward.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine import look
from api_gateway.imagine import routes as imagine_routes
from api_gateway.imagine.service import ImagineError, ImagineService
from tests.unit.imagine_fixtures import authored_lamp_form, full_config
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"

MEASURED: dict[str, Any] = {
    "total_area": 600.0,
    "overhang_area": 60.0,
    "overhang_share": 0.1,
    "com": [0.0, 5.0, 0.0],
    "support": {"points": 4, "margin_mm": 5.0, "tip_angle_deg": 45.0},
    "overlaps": [{"a": "x", "b": "y", "volume_mm3": 5.0}],
    "notes": ["Measured from the drawn triangles, with uniform density."],
}


def _config() -> dict[str, Any]:
    cfg = copy.deepcopy(full_config())
    cfg["form"] = authored_lamp_form()
    return cfg


@pytest.fixture
async def service() -> ImagineService:
    svc = ImagineService(InMemoryTwinAPI.create())
    await svc.put_studio(PROJECT, _config(), actor="test")
    return svc


async def test_a_measurement_is_recorded_against_the_revision_and_read_back(
    service: ImagineService,
) -> None:
    await service.record_measurement(PROJECT, 1, copy.deepcopy(MEASURED))
    got = await service.get_measurement(PROJECT)
    assert got is not None
    assert got["revision"] == 1 and got["measured"] == MEASURED
    assert got["method"] == "headless look run by the gateway" and got["at"]


async def test_recording_does_not_make_a_new_revision(service: ImagineService) -> None:
    await service.record_measurement(PROJECT, 1, copy.deepcopy(MEASURED))
    assert (await service.get_studio(PROJECT)).revision == 1


async def test_a_measurement_for_an_old_revision_is_refused(service: ImagineService) -> None:
    await service.put_studio(PROJECT, _config(), actor="test")  # revision 2
    with pytest.raises(ImagineError) as caught:
        await service.record_measurement(PROJECT, 1, copy.deepcopy(MEASURED))
    assert caught.value.status == 409 and "revision" in str(caught.value)


async def test_a_newer_write_drops_the_measurement_instead_of_carrying_it_forward(
    service: ImagineService,
) -> None:
    await service.record_measurement(PROJECT, 1, copy.deepcopy(MEASURED))
    await service.put_studio(PROJECT, _config(), actor="test")
    assert await service.get_measurement(PROJECT) is None


async def test_with_no_studio_there_is_nothing_to_record_against() -> None:
    svc = ImagineService(InMemoryTwinAPI.create())
    with pytest.raises(ImagineError) as caught:
        await svc.record_measurement(PROJECT, 1, copy.deepcopy(MEASURED))
    assert caught.value.status == 404
    assert await svc.get_measurement(PROJECT) is None


async def test_a_handed_over_studio_is_read_only_for_measurements_too(
    service: ImagineService,
) -> None:
    head = await service._head(PROJECT)
    assert head is not None
    head.state["status"] = "handed_over"
    await service._save(head)
    with pytest.raises(ImagineError) as caught:
        await service.record_measurement(PROJECT, 1, copy.deepcopy(MEASURED))
    assert caught.value.status == 409


async def test_only_the_fields_the_page_reports_are_kept(service: ImagineService) -> None:
    messy = {**copy.deepcopy(MEASURED), "verified": True, "authority": "approved"}
    await service.record_measurement(PROJECT, 1, messy)
    got = await service.get_measurement(PROJECT)
    assert got is not None and set(got["measured"]) == set(MEASURED)


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict",
        {**MEASURED, "overhang_share": float("nan")},
        {**MEASURED, "overhang_share": "ten"},
        {**MEASURED, "com": [1, 2]},
        {**MEASURED, "support": {"points": 4, "margin_mm": float("inf"), "tip_angle_deg": 1}},
        {**MEASURED, "overlaps": "none"},
        {**MEASURED, "overlaps": [{"a": "x"}]},
        {**MEASURED, "notes": [5]},
    ],
)
async def test_a_measurement_in_the_wrong_shape_is_refused(
    service: ImagineService, bad: Any
) -> None:
    with pytest.raises(ImagineError) as caught:
        await service.record_measurement(PROJECT, 1, bad)
    assert caught.value.status == 422
    assert await service.get_measurement(PROJECT) is None


async def test_an_oversized_measurement_is_refused(service: ImagineService) -> None:
    huge = {**MEASURED, "overlaps": [{"a": "x", "b": "y", "volume_mm3": 1.0}] * 5000}
    with pytest.raises(ImagineError) as caught:
        await service.record_measurement(PROJECT, 1, huge)
    assert caught.value.status in (413, 422)


def test_no_route_lets_a_browser_send_a_measurement() -> None:
    paths = [getattr(r, "path", "") for r in imagine_routes.router.routes]
    assert not [p for p in paths if "measure" in p.lower()], paths


# -- the tool records what its own browser measured ---------------------------------------------


def _raw(before: dict[str, Any], after: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "before": before,
        "after": after,
        "requests": {"model_calls": [], "outside": []},
        "csp": [],
        "shots": [b"\x89PNG\r\n\x1a\n"],
    }


def _summary(measured: dict[str, Any] | None, status: str = "ready") -> dict[str, Any]:
    out = look.summarize(
        {
            "status": status,
            "bbox": {"min": [0, 0, 0], "max": [1, 2, 3]},
            "triangles": 12,
            "params": {},
            "errors": [],
            "parts": [],
            "measured": measured,
        }
    )
    return out


def _server(service: ImagineService) -> Any:
    from tool_registry.tools.twin.adapter import TwinServer

    return TwinServer(twin=service.twin, imagine_studio=service)


async def test_the_look_tool_records_the_stored_forms_measurements_not_the_what_if(
    service: ImagineService, monkeypatch: pytest.MonkeyPatch
) -> None:
    what_if = copy.deepcopy(MEASURED)
    what_if["support"]["tip_angle_deg"] = 10.0

    async def fake(*a: Any, **k: Any) -> dict[str, Any]:
        raw = _raw(_summary(copy.deepcopy(MEASURED)), _summary(what_if))
        raw.update(compare={}, pixel_difference=None, requests_after_change={})
        return raw

    monkeypatch.setattr(look, "playwright_available", lambda: True)
    monkeypatch.setattr(look, "look_html", fake)
    result = await _server(service).imagine_look({"project_id": PROJECT, "set": {"arm_len": 300}})
    assert result["status"] == "ready"
    got = await service.get_measurement(PROJECT)
    assert got is not None and got["measured"]["support"]["tip_angle_deg"] == 45.0


async def test_a_look_that_measured_nothing_records_nothing(
    service: ImagineService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(*a: Any, **k: Any) -> dict[str, Any]:
        return _raw(_summary(None, status="none"))

    monkeypatch.setattr(look, "playwright_available", lambda: True)
    monkeypatch.setattr(look, "look_html", fake)
    await _server(service).imagine_look({"project_id": PROJECT})
    assert await service.get_measurement(PROJECT) is None


async def test_a_failure_to_record_never_breaks_the_look(
    service: ImagineService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(*a: Any, **k: Any) -> dict[str, Any]:
        return _raw(_summary(copy.deepcopy(MEASURED)))

    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("the twin is down")

    monkeypatch.setattr(look, "playwright_available", lambda: True)
    monkeypatch.setattr(look, "look_html", fake)
    monkeypatch.setattr(service, "record_measurement", boom)
    result = await _server(service).imagine_look({"project_id": PROJECT})
    assert result["status"] == "ready" and result["measured"] == MEASURED
