"""Component photos kept as evidence (FORGE-924, R2)."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from PIL import Image

from api_gateway.twin.claim_recorder import is_illustrative
from api_gateway.twin.component_photo import (
    DEFAULT_LICENCE_NOTICE,
    ComponentPhotoError,
    make_component_photo_recorder,
)
from mcp_core.guardrails import BOOKKEEPING
from tool_registry.tools.twin.adapter import TwinServer
from tool_registry.tools.web.image_fetch import FetchedImage, ImageStore
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"


def _jpeg() -> bytes:
    image = Image.new("RGB", (200, 120), (20, 20, 25))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def blobs() -> dict[str, bytes]:
    return {}


@pytest.fixture
def recorder(blobs: dict[str, bytes]) -> Any:
    def writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        key = f"work-products/{node_id}/{filename}"
        blobs[key] = content
        return key

    return make_component_photo_recorder(InMemoryTwinAPI.create(), blob_writer=writer)


async def _record(recorder: Any, **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "project_id": PROJECT,
        "data": _jpeg(),
        "mime": "image/jpeg",
        "width_px": 200,
        "height_px": 120,
        "view": "angled",
        "origin": "https://assets.lcsc.com/images/lcsc/900x900/C2040_front.jpg",
        "component": "RP2040",
    }
    args.update(extra)
    return dict(await recorder(**args))


async def test_a_photo_is_kept_with_its_origin_view_checksum_and_licence(
    recorder: Any, blobs: dict[str, bytes]
) -> None:
    out = await _record(recorder)
    assert out["view"] == "angled" and out["component"] == "RP2040" and not out["reused"]
    assert out["licence_notice"] == DEFAULT_LICENCE_NOTICE and out["redistributable"] is False
    assert out["image_ref"] == f"image:{out['sha256']}"
    assert list(blobs.values()) == [_jpeg()]


async def test_the_same_photo_twice_is_the_same_record(recorder: Any) -> None:
    first = await _record(recorder)
    again = await _record(recorder, view="top")
    assert again["reused"] is True and again["node_id"] == first["node_id"]


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"view": "front"}, "view is one of"),
        ({"mime": "image/svg+xml"}, "JPEG, PNG"),
        ({"origin": " "}, "origin is required"),
        ({"project_id": "not-a-project"}, "UUID"),
    ],
)
async def test_what_cannot_be_recorded_says_why(
    recorder: Any, change: dict[str, Any], why: str
) -> None:
    with pytest.raises(ComponentPhotoError, match=why):
        await _record(recorder, **change)


def test_a_photo_never_supports_a_claim() -> None:
    photo = SimpleNamespace(type=SimpleNamespace(value="component_photo"), metadata={})
    assert is_illustrative(photo)


def test_recording_a_photo_is_not_held_for_approval() -> None:
    assert "twin.record_component_photo" in BOOKKEEPING


async def test_the_tool_reads_the_fetched_image_and_its_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_IMAGE_ROOT", str(tmp_path))
    data = _jpeg()
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    url = "https://assets.lcsc.com/images/lcsc/900x900/C2040_front.jpg"
    ImageStore(tmp_path).put(
        data,
        FetchedImage(
            f"image:{sha}", sha, "image/jpeg", 200, 120, url, "2026-11-24T10:00:00+00:00", len(data)
        ),
    )
    seen: dict[str, Any] = {}

    async def record(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"node_id": str(UUID(int=1)), "reused": False}

    server = TwinServer(twin=InMemoryTwinAPI.create(), component_photo_recorder=record)
    assert "twin.record_component_photo" in server.tool_ids
    await server.record_component_photo(
        {"project_id": PROJECT, "image_ref": f"image:{sha}", "view": "top", "component": "RP2040"}
    )
    assert seen["origin"] == url and seen["data"] == data and seen["width_px"] == 200
    assert seen["retrieved_at"] == "2026-11-24T10:00:00+00:00"
    with pytest.raises(ValueError, match="no stored image"):
        await server.record_component_photo(
            {"project_id": PROJECT, "image_ref": "image:" + "0" * 64, "view": "top"}
        )
