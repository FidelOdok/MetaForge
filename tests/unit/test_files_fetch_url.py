"""files.fetch_url: keep a maker's CAD from a URL, safely (R2.5)."""

from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from tool_registry.tools.web import file_fetch
from tool_registry.tools.web.file_fetch import (
    FileFetchError,
    fetch_file,
    guess_licence,
    sniff_kind,
)

STEP = (
    b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('pi5'),'2;1');\nENDSEC;\n"
    b"DATA;\nENDSEC;\nEND-ISO-10303-21;\n"
)
MIT = (
    "MIT License\n\nCopyright (c) 2023 Raspberry Pi Ltd\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy..."
)


def _zip(members: dict[str, bytes], *, link: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _public(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(url: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(file_fetch, "assert_public_host", public)
    monkeypatch.delenv("OCCT_CONVERTER_URL", raising=False)


def _client(body: bytes, calls: list[str] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "/files/pi5.zip"})
        return httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_type_comes_from_the_bytes() -> None:
    assert sniff_kind(STEP) == "step"
    assert sniff_kind(b"%PDF-1.7\n") == "pdf"
    assert sniff_kind(b"(kicad_pcb (version 20240108)") == "kicad"
    assert sniff_kind(b"solid part\n facet normal 0 0 1\n") == "stl"
    assert sniff_kind(b"<html>not a step</html>") is None
    assert guess_licence(MIT) == "MIT"


async def test_a_zip_is_unpacked_recorded_and_cached(tmp_path: Path) -> None:
    body = _zip(
        {
            "RPi5/RPi5.step": STEP,
            "RPi5/LICENSE.txt": MIT.encode(),
            "RPi5/notes.docx": b"PK\x03\x04 a word file",
            "RPi5/inner.zip": _zip({"x.step": STEP}),
        }
    )
    calls: list[str] = []
    out = await fetch_file("https://example.com/go", client=_client(body, calls), root=tmp_path)
    assert out["final_url"] == "https://example.com/files/pi5.zip" and not out["cached"]
    (step,) = out["files"]
    assert step["kind"] == "step" and Path(step["file_path"]).read_bytes() == STEP
    assert len(step["sha256"]) == 64 and step["glb_why_not"].startswith("no OCCT converter")
    assert out["licence"]["spdx_guess"] == "MIT" and out["licence"]["file"] == "RPi5/LICENSE.txt"
    why = {s["path"]: s["why"] for s in out["skipped"]}
    assert "nested archive" in why["RPi5/inner.zip"] and "not a CAD" in why["RPi5/notes.docx"]
    assert out["next"]["commit"]["file_path"] == step["file_path"]
    manifest = json.loads((Path(out["folder"]) / "manifest.json").read_text())
    assert manifest["source_url"] == "https://example.com/go" and manifest["retrieved_at"]

    again = await fetch_file("https://example.com/go", client=_client(body, calls), root=tmp_path)
    assert again["cached"] and again["files"][0]["file_path"] == step["file_path"]
    assert len(calls) == 2  # the redirect and the file, once


async def test_a_plain_step_is_kept_under_its_name(tmp_path: Path) -> None:
    out = await fetch_file(
        "https://example.com/files/case.stp", client=_client(STEP), root=tmp_path
    )
    assert [f["path"] for f in out["files"]] == ["case.stp"] and out["licence"] is None


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (b"<html>a download page</html>", "not a STEP"),
        (_zip({"../evil.step": STEP}), "outside its folder"),
        (_zip({"/abs.step": STEP}), "outside its folder"),
        (_zip({"readme.md": b"hi"}), "holds no STEP"),
        (_zip({"bomb.step": b"ISO-10303-21;" + b"0" * 2_000_000}), "decompression bomb"),
    ],
)
async def test_what_is_not_kept_says_why(tmp_path: Path, body: bytes, why: str) -> None:
    with pytest.raises(FileFetchError, match=why):
        await fetch_file("https://example.com/files/x", client=_client(body), root=tmp_path)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("fetch-")]  # cleaned up


async def test_a_link_in_an_archive_is_skipped(tmp_path: Path) -> None:
    body = _zip({"a.step": STEP}, link="passwd")
    out = await fetch_file("https://example.com/files/x.zip", client=_client(body), root=tmp_path)
    assert {"path": "passwd", "why": "a link, not a file"} in out["skipped"]
    assert not (Path(out["folder"]) / "passwd").exists()


async def test_the_size_cap_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_FILE_FETCH_MAX_MB", "0.0001")  # 100 bytes
    with pytest.raises(FileFetchError, match="over the 0 MB limit"):
        await fetch_file(
            "https://example.com/files/x.step", client=_client(STEP * 3), root=tmp_path
        )


async def test_only_https_and_public_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_registry.tools.web.safety import UnsafeUrlError

    with pytest.raises(UnsafeUrlError, match="https URLs only"):
        await fetch_file("http://example.com/x.step", client=_client(STEP), root=tmp_path)

    async def private(url: str) -> Any:
        raise UnsafeUrlError("resolves to a private address")

    monkeypatch.setattr(file_fetch, "assert_public_host", private)
    with pytest.raises(UnsafeUrlError, match="private"):
        await fetch_file("https://intranet.example/x.step", client=_client(STEP), root=tmp_path)
