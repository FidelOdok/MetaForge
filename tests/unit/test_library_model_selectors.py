"""A maker's model that sits inside a larger maker file (2026-11-12).

Nordic publishes its chips' 3D bodies only inside the Altium libraries of its reference
layouts, and Raspberry Pi its chips only as named parts of a board's STEP. An artifact can
name how to reach the STEP (``embedded`` OLE stream steps, a ``part`` name), so the pin stays
the maker's own HTTPS file and its sha256. Such an artifact is for viewing only.
"""

from __future__ import annotations

import hashlib
import io
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest

from api_gateway.library import model_mesh
from twin_core.library.catalog import SqliteCatalog
from twin_core.library.model_contracts import validate_models

STEP = b"ISO-10303-21;\nHEADER;\nFILE_NAME('chip');\nENDSEC;\n" + b"/* pad */\n" * 600
END, FREE, FATSECT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD


def ole(tree: dict[str, Any]) -> bytes:
    """A small OLE compound file (512-byte sectors, no mini stream) holding ``tree``.

    ``tree`` maps a name to bytes (a stream, at least 4096 bytes) or to a dict (a storage).
    """
    entries: list[dict[str, Any]] = [{"name": "Root Entry", "type": 5, "kids": list(tree.items())}]
    streams: list[tuple[int, bytes]] = []

    def add(items: list[tuple[str, Any]]) -> int:
        ids = []
        for name, value in items:
            i = len(entries)
            ids.append(i)
            if isinstance(value, dict):
                entries.append({"name": name, "type": 1, "kids": list(value.items())})
            else:
                assert len(value) >= 4096, "keep test streams out of the mini stream"
                entries.append({"name": name, "type": 2, "data": value})
                streams.append((i, value))
        for a, b in zip(ids, ids[1:], strict=False):
            entries[a]["right"] = b
        for i in ids:
            if entries[i]["type"] == 1:
                entries[i]["child"] = add(entries[i]["kids"])
        return ids[0] if ids else FREE

    entries[0]["child"] = add(entries[0]["kids"])
    n_dir = (len(entries) + 3) // 4
    fat = [FATSECT] + [i + 1 if i < n_dir else END for i in range(1, n_dir + 1)]
    body = b""
    for i, data in streams:
        n = (len(data) + 511) // 512
        entries[i]["start"], entries[i]["size"] = len(fat), len(data)
        fat += [len(fat) + k + 1 for k in range(n - 1)] + [END]
        body += data + b"\0" * (n * 512 - len(data))
    assert len(fat) <= 128
    fat += [FREE] * (128 - len(fat))

    def dirent(e: dict[str, Any]) -> bytes:
        name = (e["name"] + "\0").encode("utf-16le")
        raw = bytearray(128)
        raw[: len(name)] = name
        struct.pack_into("<HBB", raw, 64, len(name), e["type"], 1)
        links = (e.get("left", FREE), e.get("right", FREE), e.get("child", FREE))
        struct.pack_into("<III", raw, 68, *links)
        struct.pack_into("<II", raw, 116, e.get("start", END), e.get("size", 0))
        return bytes(raw)

    directory = b"".join(dirent(e) for e in entries)
    directory += dirent({"name": "", "type": 0}) * (n_dir * 4 - len(entries))
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, END, 0, END, 0)
    struct.pack_into("<109I", header, 76, 0, *([FREE] * 108))
    return bytes(header) + struct.pack("<128I", *fat) + directory + body


def altium_intlib() -> bytes:
    """Nordic's layout: IntLib -> PCBLib/0.pcblib (zlib, a byte in front) -> Library/Models/6."""
    other = b"ISO-10303-21; an inductor\n" + b"x" * 5000
    pad = b"\0" * 4096
    models = {"5": zlib.compress(other) + pad, "6": zlib.compress(STEP) + pad}
    pcblib = ole({"Library": {"Models": models}})
    return ole({"PCBLib": {"0.pcblib": b"\x01" + zlib.compress(pcblib) + b"\0" * 4096}})


def zipped(member: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, data)
    return buf.getvalue()


def entry(data: bytes, **artifact: Any) -> dict[str, Any]:
    path = artifact.pop("path", "nrf52840_qiaa.IntLib")
    return {
        "name": "chip",
        "version": 1,
        "models": [
            {
                "schema_version": 1,
                "model_id": "maker_step",
                "version": "1",
                "kind": "cad",
                "engine": "step",
                "entrypoint": path,
                "usage": "the maker's model",
                "status": "active",
                "artifacts": [
                    {
                        "path": path,
                        "url": "https://maker.example/layout.zip",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                        "licence": "none stated (internal use only)",
                        "source_revision": "2026-11-12",
                        **artifact,
                    }
                ],
            }
        ],
    }


GROUP = {
    "name": "Body",
    "colour": [0.1, 0.1, 0.1],
    "colour_source": "part",
    "positions": [0, 0, 0, 7, 0, 0, 0, 7, 0, 0, 0, 0.85],
    "indices": [0, 1, 2, 0, 2, 3],
}


class Convert:
    def __init__(self, groups: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[bytes, str, str | None]] = []
        self.groups = [GROUP] if groups is None else groups

    def __call__(self, step: bytes, name: str, part: str | None = None) -> list[dict[str, Any]]:
        self.calls.append((step, name, part))
        return self.groups


def test_negative_control_the_old_reader_hands_the_converter_the_whole_altium_library() -> None:
    data = zipped("nrf52840_qiaa.IntLib", altium_intlib())
    assert not model_mesh.step_bytes(data, "nrf52840_qiaa.IntLib").startswith(b"ISO-10303-21")


def test_the_reader_finds_every_stream_by_its_path() -> None:
    streams = model_mesh.ole_streams(ole({"A": {"B": b"b" * 4096}, "C": b"c" * 5000}))
    assert set(streams) == {"A/B", "C"} and streams["C"] == b"c" * 5000


def test_a_chip_is_taken_out_of_the_makers_altium_library(tmp_path: Path) -> None:
    data = zipped("nrf52840_qiaa.IntLib", altium_intlib())
    e = entry(
        data,
        embedded=["PCBLib/0.pcblib", "Library/Models/6"],
        extracted_sha256=hashlib.sha256(STEP).hexdigest(),
    )
    convert = Convert()
    mesh = model_mesh.make_mesh(e, fetch=lambda url: data, convert=convert, cache_dir=tmp_path)
    assert convert.calls[0][0] == STEP and convert.calls[0][1] == "6.step"
    assert mesh["triangles"] == 2


def test_a_changed_embedded_model_is_refused(tmp_path: Path) -> None:
    data = zipped("nrf52840_qiaa.IntLib", altium_intlib())
    e = entry(data, embedded=["PCBLib/0.pcblib", "Library/Models/6"], extracted_sha256="0" * 64)
    with pytest.raises(model_mesh.MeshError, match="does not match"):
        model_mesh.make_mesh(e, fetch=lambda url: data, convert=Convert(), cache_dir=tmp_path)


def test_a_missing_stream_is_named(tmp_path: Path) -> None:
    data = zipped("nrf52840_qiaa.IntLib", altium_intlib())
    e = entry(data, embedded=["PCBLib/0.pcblib", "Library/Models/9"])
    with pytest.raises(model_mesh.MeshError, match="Library/Models/9"):
        model_mesh.make_mesh(e, fetch=lambda url: data, convert=Convert(), cache_dir=tmp_path)


def test_a_named_part_is_asked_of_the_converter_and_cached_apart(tmp_path: Path) -> None:
    data = zipped("PicoW.stp", STEP)
    convert = Convert()
    chip = entry(data, path="PicoW.stp", part="RPI_RP2040")
    board = entry(data, path="PicoW.stp")
    model_mesh.make_mesh(chip, fetch=lambda url: data, convert=convert, cache_dir=tmp_path)
    model_mesh.make_mesh(board, fetch=lambda url: data, convert=convert, cache_dir=tmp_path)
    assert [c[2] for c in convert.calls] == ["RPI_RP2040", None]  # same file, two meshes


def test_a_part_the_file_does_not_hold_is_named(tmp_path: Path) -> None:
    data = zipped("PicoW.stp", STEP)
    with pytest.raises(model_mesh.MeshError, match="RP9999"):
        model_mesh.make_mesh(
            entry(data, path="PicoW.stp", part="RP9999"),
            fetch=lambda url: data,
            convert=Convert([]),
            cache_dir=tmp_path,
        )


def test_the_library_keeps_the_selectors_and_old_models_keep_their_exact_content() -> None:
    data = zipped("PicoW.stp", STEP)
    kept = validate_models(entry(data, path="PicoW.stp", part="RPI_RP2040")["models"])
    assert kept[0]["artifacts"][0]["part"] == "RPI_RP2040"
    plain = validate_models(entry(data, path="PicoW.stp")["models"])
    assert not {"embedded", "part", "extracted_sha256"} & set(plain[0]["artifacts"][0])
    catalog = SqliteCatalog()
    saved = catalog.register(
        "component", {"name": "chip", "family": "electronic", "origin": "purchased", "models": kept}
    ).as_dict()
    assert saved["models"][0]["artifacts"][0]["part"] == "RPI_RP2040"


@pytest.mark.parametrize("steps", [["../x"], ["a//b"], [""]])
def test_a_stream_path_that_climbs_or_is_empty_is_refused(steps: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_models(entry(b"x", embedded=steps)["models"])


async def test_a_selected_model_never_goes_to_an_engineering_tool(tmp_path: Path) -> None:
    from tool_registry.tools.library import model_resolver

    data = zipped("rpi_pico2.step", STEP)
    e = entry(data, path="rpi_pico2.step", part="rpi_rp2350a", licence="MIT")

    class Service:
        async def get_entry(self, kind: str, name: str) -> dict[str, Any]:
            return {"entry": {"models": e["models"]}}

    with pytest.raises(ValueError, match="viewing only"):
        await model_resolver.resolve(
            Service(),
            {"ref": "chip", "model_id": "maker_step", "version": "1", "engine": "step"},
            workspace=tmp_path,
        )
