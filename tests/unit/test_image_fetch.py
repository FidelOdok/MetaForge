"""image.fetch: a component photo from a URL or a part number (FORGE-923, R1)."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image

from tool_registry.tools.web import image_fetch
from tool_registry.tools.web.image_fetch import (
    ImageFetchError,
    ImageStore,
    fetch_image,
    sniff_mime,
)
from tool_registry.tools.web.mcp_adapter import WebMcpServer

URL = "https://www.mouser.com/images/adafruit/lrg/4480_SPL.jpg"


def _photo(fmt: str = "JPEG", size: tuple[int, int] = (320, 240), flat: bool = False) -> bytes:
    image = Image.new("RGB", size, (30, 90, 160))
    if not flat:
        # A part on a light background: a dark body over a gradient, like a product shot.
        for x in range(size[0]):
            for y in range(size[1]):
                inside = size[0] // 4 < x < 3 * size[0] // 4 and size[1] // 4 < y < 3 * size[1] // 4
                image.putpixel((x, y), (20, 20, 25) if inside else (200 + x % 50, 200, 210))
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _public(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(_url: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(image_fetch, "assert_public_host", public)


def _client(body: bytes, ctype: str = "image/jpeg", seen: list[str] | None = None, **extra: Any):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if "redirect" in extra and not str(request.url).endswith("/final.jpg"):
            return httpx.Response(302, headers={"location": "/final.jpg"})
        return httpx.Response(200, content=body, headers={"content-type": ctype})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_types_come_from_the_bytes() -> None:
    assert sniff_mime(_photo("JPEG")) == "image/jpeg"
    assert sniff_mime(_photo("PNG")) == "image/png"
    assert sniff_mime(_photo("WEBP")) == "image/webp"
    assert sniff_mime(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None


async def test_a_photo_is_fetched_described_stored_and_cached(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    seen: list[str] = []
    out = await fetch_image(URL, store=store, client=_client(_photo(), seen=seen))
    assert out["status"] == "ok" and out["mime"] == "image/jpeg"
    assert (out["width_px"], out["height_px"]) == (320, 240)
    assert out["image_ref"] == f"image:{out['sha256']}" and out["source_url"] == URL
    data, mime = store.read(out["image_ref"])
    assert mime == "image/jpeg" and len(data) == out["bytes"]
    again = await fetch_image(URL, store=store, client=_client(_photo(), seen=seen))
    assert again["cached"] is True and again["sha256"] == out["sha256"] and len(seen) == 1


async def test_a_redirect_is_followed_and_both_addresses_are_cached(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    out = await fetch_image(URL, store=store, client=_client(_photo(), redirect=True))
    assert out["source_url"].endswith("/final.jpg")
    assert store.lookup_url(URL) is not None


@pytest.mark.parametrize(
    ("body", "ctype", "why"),
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>", "image/svg+xml", "SVG"),
        (b"<html>Access denied</html>", "text/html", "not a JPEG"),
        (_photo("PNG"), "image/jpeg", "declared image/jpeg but the bytes are image/png"),
        (b"\xff\xd8\xff\xe0garbage", "image/jpeg", "not a readable image"),
    ],
)
async def test_what_is_not_a_good_image_is_refused(
    tmp_path: Path, body: bytes, ctype: str, why: str
) -> None:
    with pytest.raises(ImageFetchError, match=why):
        await fetch_image(URL, store=ImageStore(tmp_path), client=_client(body, ctype))


async def test_an_oversized_image_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_fetch, "MAX_IMAGE_BYTES", 1000)
    with pytest.raises(ImageFetchError, match="limit"):
        await fetch_image(URL, store=ImageStore(tmp_path), client=_client(_photo()))


@pytest.mark.parametrize(
    ("url", "body", "why"),
    [
        ("https://www.mouser.com/images/no_image.jpg", _photo(), "placeholder"),
        (
            "https://static.lcsc.com/feassets/pc/images/product/no_goods_pic6.jpg",
            _photo(),
            "placeholder",
        ),
        (URL, _photo(size=(32, 32)), "32 x 32"),
        (URL, _photo(flat=True), "flat colour"),
    ],
)
async def test_a_placeholder_is_no_photo(tmp_path: Path, url: str, body: bytes, why: str) -> None:
    out = await fetch_image(url, store=ImageStore(tmp_path), client=_client(body))
    assert out["status"] == "no_photo" and why in out["reason"] and "image_ref" not in out


async def test_a_private_address_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    from tool_registry.tools.web.safety import UnsafeUrlError

    with pytest.raises(UnsafeUrlError):
        await fetch_image("http://127.0.0.1/photo.jpg", store=ImageStore(tmp_path))


# -- the tool, by part number --------------------------------------------------------------


class _Distributor:
    def __init__(self, name: str, image_url: str | None, exists: bool = True) -> None:
        self._name, self._url, self._exists = name, image_url, exists

    @property
    def name(self) -> str:
        return self._name

    async def get_part_details(self, mpn: str) -> Any:
        return SimpleNamespace(image_url=self._url) if self._exists else None


async def test_a_part_number_uses_the_first_distributor_with_a_photo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    async def fake_fetch(url: str, **_: Any) -> dict[str, Any]:
        asked.append(url)
        return {"status": "ok", "image_ref": "image:" + "0" * 64, "source_url": url}

    monkeypatch.setattr("tool_registry.tools.web.mcp_adapter.fetch_image", fake_fetch)
    server = WebMcpServer(
        provider=None,
        distributors=[_Distributor("Mouser", None), _Distributor("DigiKey", URL)],
        image_store=ImageStore(tmp_path),
    )
    out = await server.handle_image_fetch({"part_number": "4480"})
    assert asked == [URL] and out["distributor"] == "DigiKey" and out["part_number"] == "4480"


async def test_no_photo_anywhere_says_which_distributors_were_asked(tmp_path: Path) -> None:
    server = WebMcpServer(
        provider=None,
        distributors=[_Distributor("Mouser", None, exists=False), _Distributor("DigiKey", None)],
        image_store=ImageStore(tmp_path),
    )
    with pytest.raises(ValueError, match="Mouser: no record; DigiKey: no photo"):
        await server.handle_image_fetch({"part_number": "NOPE-1"})


async def test_exactly_one_of_url_or_part_number(tmp_path: Path) -> None:
    server = WebMcpServer(provider=None, image_store=ImageStore(tmp_path))
    for args in ({}, {"url": URL, "part_number": "4480"}):
        with pytest.raises(ValueError, match="exactly one"):
            await server.handle_image_fetch(args)
