"""Blender render adapter and its GPL boundary (FORGE-628).

CI has no Blender, so the adapter runs against a fake ``blender`` that keeps
the same contract (reads the job after ``--``, writes the PNGs and
result.json). The real Blender 5.2.2 LTS run is recorded in the PR.
"""

from __future__ import annotations

import base64
import re
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from mcp_core.annotations import OPEN_WORLD, READ_ONLY
from tool_registry.tools.blender.adapter import BlenderServer
from tool_registry.tools.blender.config import BlenderConfig

REPO = Path(__file__).resolve().parents[2]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

FAKE = """#!{python}
import json, os, sys, time
job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
mode = os.environ.get("FAKE_BLENDER_MODE", "ok")
if mode == "fail":
    sys.stderr.write("Error: cannot import model\\n"); sys.exit(1)
if mode == "slow":
    time.sleep(30)
out = job["out_dir"]
open(os.path.join(out, "job_seen.json"), "w").write(json.dumps(job))
images = []
views = job.get("views") or []
for v in views:
    frames = range(job["turntable_frames"])
    names = [f"turntable_{{i:03d}}" for i in frames] if v == "turntable" else [v]
    for n in names:
        if mode != "missing":
            open(os.path.join(out, n + ".png"), "wb").write({png!r})
        images.append({{"view": n, "file": n + ".png"}})
blend = None
if job.get("save_blend"):
    blend = "scene.blend"
    open(os.path.join(out, blend), "wb").write(b"BLENDER-fake")
if mode != "noresult":
    json.dump({{"images": images, "blend": blend, "mesh_objects": 2,
               "coloured_objects": list(job.get("colours", {{}})),
               "bounds_min": [0, 0, 0], "bounds_max": [80, 50, 26],
               "blender_version": "5.2.2 LTS"}}, open(os.path.join(out, "result.json"), "w"))
"""


@pytest.fixture
def fake_blender(tmp_path: Path) -> Path:
    path = tmp_path / "blender"
    path.write_text(FAKE.format(python=sys.executable, png=PNG))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def server(tmp_path: Path, fake_blender: Path) -> BlenderServer:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "part.glb").write_bytes(b"glTF-fake")
    return BlenderServer(
        BlenderConfig(blender_binary=str(fake_blender), work_dir=str(workspace), timeout_seconds=10)
    )


@pytest.mark.asyncio
async def test_render_returns_pngs_inline_and_leaves_nothing(server: BlenderServer) -> None:
    out = await server.render(
        {
            "input_file": "part.glb",
            "views": ["iso", "top"],
            "resolution": [320, 240],
            "colours": {"lid": [0.1, 0.3, 0.8, 1.0]},
        }
    )
    assert [i["view"] for i in out["images"]] == ["iso", "top"]
    assert base64.b64decode(out["images"][0]["png_base64"]) == PNG
    assert out["images"][0]["width"] == 320
    assert out["coloured_objects"] == ["lid"]
    assert out["blender_version"] == "5.2.2 LTS"
    assert list(Path(server.config.work_dir).iterdir()) == [
        Path(server.config.work_dir) / "part.glb"
    ]


@pytest.mark.asyncio
async def test_turntable_frames_and_inline_models(server: BlenderServer) -> None:
    out = await server.render(
        {
            "model_base64": base64.b64encode(b"solid fake").decode(),
            "model_format": "stl",
            "views": ["turntable"],
            "turntable_frames": 4,
        }
    )
    assert [i["view"] for i in out["images"]] == [f"turntable_{i:03d}" for i in range(4)]


@pytest.mark.asyncio
async def test_export_blend(server: BlenderServer) -> None:
    out = await server.export_blend({"input_file": "part.glb"})
    assert base64.b64decode(out["blend_base64"]) == b"BLENDER-fake"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"input_file": "../etc/passwd"}, "inside the adapter workspace"),
        ({"input_file": "missing.glb"}, "does not exist"),
        ({"model_base64": "AAAA", "model_format": "step"}, "model_format must be"),
        ({}, "give input_file"),
        ({"input_file": "part.glb", "views": ["side"]}, "unknown views"),
        ({"input_file": "part.glb", "resolution": [8000, 8000]}, "larger than"),
        ({"input_file": "part.glb", "views": ["turntable"], "turntable_frames": 40}, "at most"),
        ({"input_file": "part.glb", "colours": {"lid": [2, 0, 0, 1]}}, "colours must map"),
        (
            {"input_file": "part.glb", "colours": {"lid": {"rgba": [0, 0, 0, 1], "roughness": 3}}},
            "roughness must be between 0 and 1",
        ),
        (
            {"input_file": "part.glb", "colours": {"lid": {"rgba": [0, 0, 0, 1], "gloss": 1}}},
            "unknown fields gloss",
        ),
        ({"input_file": "part.glb", "model_up_axis": "x"}, "model_up_axis must be y or z"),
    ],
)
async def test_bad_requests_are_refused(
    server: BlenderServer, args: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        await server.render(args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "match"),
    [
        ("fail", "exited with code 1: Error: cannot import model"),
        ("noresult", "wrote no result"),
        ("missing", "did not write it"),
    ],
)
async def test_blender_failures_are_reported(
    server: BlenderServer, monkeypatch: pytest.MonkeyPatch, mode: str, match: str
) -> None:
    monkeypatch.setenv("FAKE_BLENDER_MODE", mode)
    with pytest.raises(ValueError, match=match):
        await server.render({"input_file": "part.glb"})


@pytest.mark.asyncio
async def test_a_slow_blender_is_stopped(
    server: BlenderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_BLENDER_MODE", "slow")
    server.config.timeout_seconds = 1
    with pytest.raises(ValueError, match="did not finish within 1 s"):
        await server.render({"input_file": "part.glb"})


@pytest.mark.asyncio
async def test_no_blender_installed_says_so(tmp_path: Path) -> None:
    server = BlenderServer(
        BlenderConfig(blender_binary=str(tmp_path / "nope"), work_dir=str(tmp_path))
    )
    (tmp_path / "m.glb").write_bytes(b"x")
    with pytest.raises(ValueError, match="not installed here"):
        await server.render({"input_file": "m.glb"})


def test_the_job_the_script_gets(server: BlenderServer, tmp_path: Path) -> None:
    """The keys render_scene.py reads are the keys the adapter writes."""
    script = (REPO / "tool_registry/tools/blender/bpy_scripts/render_scene.py").read_text()
    for key in (
        "input",
        "out_dir",
        "views",
        "turntable_frames",
        "resolution",
        "samples",
        "projection",
        "transparent",
        "background",
        "colours",
        "save_blend",
    ):
        assert f'"{key}"' in script, key


def test_no_metaforge_code_imports_bpy_outside_the_gpl_folder() -> None:
    """The GPL boundary: only bpy_scripts/ may import Blender's API."""
    gpl = REPO / "tool_registry/tools/blender/bpy_scripts"
    pattern = re.compile(r"^\s*(import bpy|from bpy\b|import mathutils|from mathutils\b)", re.M)
    offenders = []
    for root in (
        "tool_registry",
        "api_gateway",
        "metaforge",
        "twin_core",
        "mcp_core",
        "domain_agents",
        "orchestrator",
        "skill_registry",
        "digital_twin",
    ):
        for path in (REPO / root).rglob("*.py"):
            if gpl in path.parents:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_every_gpl_script_says_so_and_ships_the_licence() -> None:
    gpl = REPO / "tool_registry/tools/blender/bpy_scripts"
    assert "GNU GENERAL PUBLIC LICENSE" in (gpl / "LICENSE").read_text()
    assert "Version 3" in (gpl / "LICENSE").read_text()
    for path in gpl.glob("*.py"):
        assert "SPDX-License-Identifier: GPL-3.0-or-later" in path.read_text(), path.name


def test_the_image_installs_the_lts_build_it_names() -> None:
    dockerfile = (REPO / "tool_registry/tools/blender/Dockerfile").read_text()
    notice = (REPO / "tool_registry/tools/blender/BLENDER_NOTICE.md").read_text()
    version = re.search(r"ARG BLENDER_VERSION=(\S+)", dockerfile)
    sha = re.search(r"ARG BLENDER_SHA256=(\S+)", dockerfile)
    assert version and sha
    assert f"blender-{version.group(1)}.tar.xz" in notice  # the matching source
    assert sha.group(1) in notice
    assert "sha256sum -c" in dockerfile


def test_annotations() -> None:
    for tool in ("blender.render", "blender.export_blend"):
        assert tool in READ_ONLY
        assert tool not in OPEN_WORLD


def test_a_colour_can_carry_its_surface(server: BlenderServer) -> None:
    """FORGE-632: an appearance sets roughness and metallic as well as colour."""
    scene = server._scene(
        {
            "colours": {
                "lid": [0.1, 0.3, 0.8, 1],
                "base": {"rgba": [0.1, 0.1, 0.1, 1], "roughness": 0.45, "metallic": 0},
            }
        }
    )
    assert scene["colours"] == {
        "lid": {"rgba": [0.1, 0.3, 0.8, 1.0]},
        "base": {"rgba": [0.1, 0.1, 0.1, 1.0], "roughness": 0.45, "metallic": 0.0},
    }
