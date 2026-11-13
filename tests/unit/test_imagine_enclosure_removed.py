"""The enclosure template is gone (plan C8): every form is authored.

A studio written before the removal still exists in the twin. It is never deleted by code,
it stays readable, and it says plainly that it cannot be opened or drawn any more and what to
do: write a new authored studio. Nothing new can be written with the enclosure template.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine.service import ImagineError, ImagineService
from tests.unit.imagine_fixtures import authored_lamp_form, full_config
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"
SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "api_gateway"
    / "imagine"
    / "formgraph"
    / "data"
    / "product.schema.json"
)


def _legacy_state() -> dict[str, Any]:
    config = copy.deepcopy(full_config())
    config["form"] = {"template": "enclosure"}
    return {
        "config": config,
        "status": "draft",
        "collections": {},
        "events": {},
        "handover": None,
        "rework": None,
        "warnings": [],
    }


async def _service_with_legacy_studio() -> ImagineService:
    service = ImagineService(InMemoryTwinAPI.create())
    await service._write_revision(PROJECT, _legacy_state(), actor="test", reason="from before")
    return service


async def test_nothing_new_can_be_written_with_the_enclosure_template() -> None:
    service = ImagineService(InMemoryTwinAPI.create())
    config = copy.deepcopy(full_config())
    config["form"] = {"template": "enclosure"}
    with pytest.raises(ImagineError) as caught:
        await service.put_studio(PROJECT, config, actor="test")
    assert caught.value.status == 422
    assert "removed" in json.dumps(caught.value.detail)
    assert (await service.get_studio(PROJECT)).status == "none"


async def test_a_legacy_studio_is_still_readable_and_nothing_deletes_it() -> None:
    service = await _service_with_legacy_studio()
    studio = await service.get_studio(PROJECT)
    assert studio.status == "draft" and studio.revision == 1
    assert (studio.config or {})["form"] == {"template": "enclosure"}


async def test_a_legacy_studio_cannot_be_drawn_and_says_what_to_do() -> None:
    service = await _service_with_legacy_studio()
    with pytest.raises(ImagineError) as caught:
        await service.engine_html(PROJECT)
    assert caught.value.status == 410
    message = str(caught.value)
    assert "enclosure" in message and "removed" in message and "authored" in message


async def test_a_new_authored_studio_can_replace_a_legacy_one() -> None:
    service = await _service_with_legacy_studio()
    config = copy.deepcopy(full_config())
    config["form"] = authored_lamp_form()
    response = await service.put_studio(PROJECT, config, actor="test")
    assert response.revision == 2 and response.config["form"]["template"] == "authored"
    assert "<html" in (await service.engine_html(PROJECT)).lower()


def test_the_schema_offers_only_the_authored_template() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["form"]["properties"]["template"]["enum"] == ["authored"]
