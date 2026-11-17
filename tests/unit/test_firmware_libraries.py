"""Libraries for a firmware build come only from the image (FORGE-889).

A build has no network. The night light's firmware needs Adafruit NeoPixel and BH1750 on the
XIAO ESP32S3 with Arduino; the image installs them into an offline library folder, and a
build names the ones it uses from there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_firmware_adapter import _server, fake_pio  # noqa: F401 - fixture


def _libraries(tmp_path: Path) -> Path:
    root = tmp_path / "offline-libs"
    neo = root / "Adafruit NeoPixel"
    neo.mkdir(parents=True)
    (neo / "library.properties").write_text("name=Adafruit NeoPixel\nversion=1.15.5\n")
    bh = root / "BH1750"
    bh.mkdir()
    (bh / "library.json").write_text(json.dumps({"name": "BH1750", "version": "1.3.0"}))
    return root


def test_the_installed_libraries_are_listed_with_their_versions(tmp_path: Path) -> None:
    server = _server(tmp_path, library_dir=str(_libraries(tmp_path)))
    assert server.available_libraries() == [
        {"name": "Adafruit NeoPixel", "version": "1.15.5", "folder": "Adafruit NeoPixel"},
        {"name": "BH1750", "version": "1.3.0", "folder": "BH1750"},
    ]
    # no folder, no libraries
    assert _server(tmp_path, library_dir=str(tmp_path / "none")).available_libraries() == []


async def test_a_build_names_libraries_from_the_image(tmp_path: Path, fake_pio: str) -> None:  # noqa: F811
    libs = _libraries(tmp_path)
    server = _server(tmp_path, pio=fake_pio, library_dir=str(libs))
    out = await server.build(
        {
            "files": [
                {
                    "path": "src/main.cpp",
                    "content": "#include <BH1750.h>\nvoid setup(){}\nvoid loop(){}",
                }
            ],
            "board": "esp32dev",
            "framework": "arduino",
            "libraries": ["bh1750", "Adafruit NeoPixel"],
        }
    )
    project = tmp_path / Path(out["elf_path"]).parents[3]
    ini = (project / "platformio.ini").read_text()
    assert "framework = arduino" in ini
    assert f"lib_extra_dirs = {libs}" in ini


async def test_a_library_the_image_lacks_is_refused_with_what_it_has(
    tmp_path: Path,
    fake_pio: str,  # noqa: F811
) -> None:
    server = _server(tmp_path, pio=fake_pio, library_dir=str(_libraries(tmp_path)))
    with pytest.raises(
        ValueError, match="FastLED.*installed: Adafruit NeoPixel 1.15.5, BH1750 1.3.0"
    ):
        await server.build(
            {
                "files": [{"path": "src/main.cpp", "content": ""}],
                "board": "esp32dev",
                "framework": "arduino",
                "libraries": ["FastLED"],
            }
        )


async def test_without_libraries_nothing_is_added(tmp_path: Path, fake_pio: str) -> None:  # noqa: F811
    server = _server(tmp_path, pio=fake_pio, library_dir=str(_libraries(tmp_path)))
    out = await server.build(
        {
            "files": [{"path": "src/main.c", "content": "int main(void){for(;;);}"}],
            "board": "disco_f407vg",
            "framework": "stm32cube",
        }
    )
    ini = (tmp_path / Path(out["elf_path"]).parents[3] / "platformio.ini").read_text()
    assert "lib_extra_dirs" not in ini


def test_the_image_seeds_the_xiao_with_arduino_and_the_libraries() -> None:
    seed = Path(__file__).parents[2] / "tool_registry" / "tools" / "firmware" / "seed"
    ini = (seed / "esp32-arduino" / "platformio.ini").read_text()
    assert "board = seeed_xiao_esp32s3" in ini and "framework = arduino" in ini
    assert "adafruit/Adafruit NeoPixel@1.15.5" in ini and "claws/BH1750@1.3.0" in ini
    assert "board = seeed_xiao_esp32s3" in (seed / "esp32" / "platformio.ini").read_text()
    dockerfile = (seed.parent / "Dockerfile").read_text()
    assert "pio run -d /tmp/seed/esp32-arduino" in dockerfile and "offline-libs" in dockerfile
