"""KiCad Phase 2 generation, live: real kicad-cli, pcbnew and Freerouting (FORGE-683).

Runs where the tools are -- the kicad-adapter image -- and skips anywhere
else. From a checkout::

    docker run --rm --network none -v "$PWD:/src" -w /src -e PYTHONPATH=/src \\
        --entrypoint bash ghcr.io/fidelodok/metaforge-kicad-adapter:latest \\
        -c "pip install -q pytest pytest-asyncio && python -m pytest -q -p no:cacheprovider \\
            --noconftest tests/integration/test_kicad_generation_live.py"

Each circuit goes schematic -> ERC + netlist check -> board -> Freerouting ->
DRC with schematic parity, judged by KiCad, not by this test's own reading
of the files.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_kicad_generation import LDO_LED
from tool_registry.tools.kicad.adapter import KicadServer
from tool_registry.tools.kicad.config import KicadConfig

pytestmark = pytest.mark.skipif(
    shutil.which("kicad-cli") is None or not Path("/usr/share/kicad/symbols").is_dir(),
    reason="needs kicad-cli and the KiCad libraries (the kicad-adapter image)",
)

BOARD = {
    "width_mm": 30,
    "height_mm": 22,
    "ground_pour": "GND",
    "power_nets": ["VIN", "+3V3", "GND"],
    "power_track_mm": 0.5,
    "mounting_holes_mm": 3.2,
}

OPAMP_BUFFER: dict[str, Any] = {
    "name": "opamp_buffer",
    "components": [
        {
            "ref": "J1",
            "symbol": "Connector_Generic:Conn_01x03",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
            "value": "IO",
        },
        {
            "ref": "U1",
            "symbol": "Amplifier_Operational:LM358",
            "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
            "value": "LM358",
        },
        {
            "ref": "C1",
            "symbol": "Device:C",
            "footprint": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF",
        },
        {
            "ref": "R1",
            "symbol": "Device:R",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "value": "10k",
        },
        {
            "ref": "R2",
            "symbol": "Device:R",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "value": "10k",
        },
    ],
    "nets": [
        {"name": "+5V", "nodes": ["J1.1", "U1.8", "C1.1", "R1.1"]},
        {"name": "GND", "nodes": ["J1.3", "U1.4", "C1.2", "R2.2"]},
        {"name": "MID", "nodes": ["R1.2", "R2.1", "U1.3"]},
        {"name": "OUT", "nodes": ["U1.1", "U1.2", "J1.2"]},
    ],
    "board": {"width_mm": 25, "height_mm": 20, "ground_pour": "GND"},
}


async def call(server: KicadServer, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """A tool call through the adapter's JSON-RPC handler, as the gateway makes it."""
    resp = json.loads(
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tool/call",
                    "params": {"tool_id": tool, "arguments": args},
                }
            )
        )
    )
    if "error" in resp:
        raise ValueError(resp["error"].get("data", {}).get("details") or resp["error"])
    data: dict[str, Any] = resp["result"]["data"]
    return data


@pytest.fixture
def server(tmp_path: Path) -> KicadServer:
    return KicadServer(KicadConfig(work_dir=str(tmp_path)))


@pytest.mark.parametrize(
    "circuit",
    [{**copy.deepcopy(LDO_LED), "board": BOARD}, OPAMP_BUFFER],
    ids=["ldo_led", "opamp_buffer"],
)
async def test_schematic_board_and_routing_pass_kicads_own_checks(
    server: KicadServer, circuit: dict[str, Any]
) -> None:
    sch = await call(server, "kicad.generate_schematic", {"circuit": circuit})
    assert sch["verdict"] == "pass", sch["erc"]
    assert sch["erc"]["errors"] == 0
    assert sch["netlist_check"]["mismatched"] == []
    assert sorted(sch["netlist_check"]["matched"]) == sorted(n["name"] for n in circuit["nets"])

    pcb = await call(server, "kicad.generate_pcb", {"circuit": circuit})
    assert pcb["verdict"] == "pass" and pcb["unplaced"] == []
    assert pcb["footprints"] == len(circuit["components"])

    routed = await call(
        server, "kicad.autoroute_pcb", {"pcb_file": pcb["pcb_file"], "ground_pour": "GND"}
    )
    assert routed["verdict"] == "pass", routed
    assert routed["unrouted"] == 0
    assert routed["drc"]["errors"] == 0
    assert routed["schematic_parity_checked"] and routed["schematic_parity"] == []
    assert routed["tracks"] > 0
    # The caller's board is copied, never modified.
    assert routed["pcb_file"] != pcb["pcb_file"]


async def test_unknown_footprint_is_refused_by_name(server: KicadServer) -> None:
    circuit = copy.deepcopy(OPAMP_BUFFER)
    circuit["components"][2]["footprint"] = "Capacitor_SMD:C_0000_NoSuch"
    with pytest.raises(ValueError, match="C1: footprint Capacitor_SMD:C_0000_NoSuch"):
        await call(server, "kicad.generate_pcb", {"circuit": circuit})


async def _routed_board(server: KicadServer, circuit: dict[str, Any]) -> dict[str, Any]:
    pcb = await call(server, "kicad.generate_pcb", {"circuit": circuit})
    return await call(
        server, "kicad.autoroute_pcb", {"pcb_file": pcb["pcb_file"], "ground_pour": "GND"}
    )


async def test_3d_export_and_render(server: KicadServer) -> None:
    """FORGE-684: STEP and GLB with the library's 3D models, and a rendered view."""
    routed = await _routed_board(server, {**copy.deepcopy(LDO_LED), "board": BOARD})
    for fmt in ("step", "glb"):
        out = await call(server, "kicad.export_3d", {"pcb_file": routed["pcb_file"], "format": fmt})
        assert out["complete"] and out["missing_models"] == [], out
        assert out["models_referenced"] >= 6 and out["bytes"] > 50_000
    render = await call(
        server, "kicad.render_pcb", {"pcb_file": routed["pcb_file"], "view": "iso", "inline": True}
    )
    assert render["png_base64"].startswith("iVBORw0KGgo")  # PNG signature
    assert render["bytes"] > 20_000


async def test_a_part_without_a_3d_model_is_named(server: KicadServer) -> None:
    circuit = copy.deepcopy(OPAMP_BUFFER)
    # A family the image does not carry models for.
    circuit["components"][0]["footprint"] = (
        "Connector_Molex:Molex_KK-254_AE-6410-03A_1x03_P2.54mm_Vertical"
    )
    pcb = await call(server, "kicad.generate_pcb", {"circuit": circuit})
    out = await call(server, "kicad.export_3d", {"pcb_file": pcb["pcb_file"]})
    assert not out["complete"]
    assert [m["ref"] for m in out["missing_models"]] == ["J1"]


async def test_update_components(server: KicadServer) -> None:
    circuit = {**copy.deepcopy(LDO_LED), "board": BOARD}
    pcb = await call(server, "kicad.generate_pcb", {"circuit": circuit})
    upd = await call(
        server,
        "kicad.update_components",
        {
            "schematic_file": pcb["schematic_file"],
            "pcb_file": pcb["pcb_file"],
            "changes": [
                {"ref": "R1", "value": "2.2k", "fields": {"MPN": "RC0603FR-072K2L"}},
                {"ref": "C1", "footprint": "Capacitor_SMD:C_1206_3216Metric"},
            ],
        },
    )
    assert upd["updated"] == ["R1", "C1"] and upd["erc"]["errors"] == 0
    assert upd["board_stale"] and upd["footprint_changes"] == ["C1"]
    bom = await call(server, "kicad.export_bom", {"schematic_file": upd["schematic_file"]})
    values = {r for p in bom["parts"] for r in p["references"] if p["value"] == "2.2k"}
    assert values == {"R1"}
    # The original schematic is untouched.
    original = Path(pcb["schematic_file"]).read_text()
    assert '"2.2k"' not in original
    with pytest.raises(ValueError, match="no such reference in the schematic: R9"):
        await call(
            server,
            "kicad.update_components",
            {"schematic_file": pcb["schematic_file"], "changes": [{"ref": "R9", "value": "1"}]},
        )
