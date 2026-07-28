"""Deterministic multi-part assembly builder + CLI (MET-10)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from api_gateway.cad.builder import build_assembly
from cli.forge_cli.cad import handle_cad


class _Bridge:
    """Fake MCP bridge that walks the author -> assemble -> export sequence."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._n = 0

    async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        if tool == "freecad.open_session":
            return {"status": "ok", "data": {"session_id": "s1"}}
        if tool == "freecad.create_primitive":
            self._n += 1
            return {"status": "ok", "data": {"obj_id": f"primitive_{self._n}"}}
        if tool == "freecad.transform_object":
            return {"status": "ok", "data": {}}
        if tool == "freecad.create_assembly":
            return {"status": "ok", "data": {"obj_id": "assembly_9"}}
        if tool == "freecad.add_part_to_assembly":
            return {"status": "ok", "data": {}}
        if tool == "freecad.export_model":
            return {"status": "ok", "data": {"step_base64": "U1RFUA=="}}
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_build_assembly_authors_all_parts_and_commits() -> None:
    recorded: dict[str, Any] = {}

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        recorded.update(kwargs)
        return {"node_id": "n1", "model_url": "/v1/twin/nodes/n1/model"}

    bridge = _Bridge()
    parts = [
        {
            "name": "Base Plate",
            "kind": "box",
            "parameters": {"width": 100, "length": 100, "height": 6},
        },
        {
            "name": "Yaw Housing",
            "kind": "cylinder",
            "parameters": {"radius": 20, "height": 45},
            "position": [50, 50, 6],
        },
        {
            "name": "Tripod Boss",
            "kind": "cylinder",
            "parameters": {"radius": 10, "height": 8},
            "position": [50, 50, -8],
        },
    ]
    rec = await build_assembly(
        bridge=bridge, recorder=recorder, name="Gimbal Base", parts=parts, project_id="p1"
    )

    assert rec["node_id"] == "n1"
    # 3 primitives created, 3 added to the assembly, 2 transforms (parts with position)
    assert bridge.calls.count("freecad.create_primitive") == 3
    assert bridge.calls.count("freecad.add_part_to_assembly") == 3
    assert bridge.calls.count("freecad.transform_object") == 2
    assert bridge.calls.count("freecad.create_assembly") == 1
    # the STEP was committed through the recorder with the assembly metadata
    assert recorded["step_base64"] == "U1RFUA=="
    assert recorded["name"] == "Gimbal Base"
    assert recorded["extra_metadata"]["part_count"] == 3


@pytest.mark.asyncio
async def test_build_assembly_raises_when_export_empty() -> None:
    class _NoStep(_Bridge):
        async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            if tool == "freecad.export_model":
                return {"status": "ok", "data": {}}  # no step_base64
            return await super().invoke(tool, args)

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        return {"node_id": "x"}

    with pytest.raises(RuntimeError, match="no STEP"):
        await build_assembly(
            bridge=_NoStep(),
            recorder=recorder,
            name="X",
            parts=[{"name": "P", "kind": "box", "parameters": {}}],
        )


class _CadClient:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def create_assembly(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.sent = spec
        return {
            "node_id": "n1",
            "model_url": "/v1/twin/nodes/n1/model",
            "part_count": len(spec["parts"]),
        }


def test_cli_cad_build_reads_spec_and_posts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = {"name": "Gimbal Base", "parts": [{"name": "Base", "kind": "box", "parameters": {}}]}
    spec_file = tmp_path / "gimbal.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")

    client = _CadClient()
    args = argparse.Namespace(cad_command="build", spec=str(spec_file), project_id="p1")
    handle_cad(args, client)

    assert client.sent["name"] == "Gimbal Base"
    assert client.sent["project_id"] == "p1"  # --project-id merged in
    out = capsys.readouterr().out
    assert "node n1" in out and "/v1/twin/nodes/n1/model" in out


def test_cli_cad_build_rejects_bad_spec(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec_file = tmp_path / "bad.json"
    spec_file.write_text(json.dumps({"name": "no parts"}), encoding="utf-8")
    args = argparse.Namespace(cad_command="build", spec=str(spec_file), project_id=None)
    handle_cad(args, _CadClient())
    assert "must be a JSON object with 'name' and 'parts'" in capsys.readouterr().err
