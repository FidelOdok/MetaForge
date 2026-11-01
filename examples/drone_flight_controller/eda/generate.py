"""Generate the drone flight controller's KiCad project from circuit.json (FORGE-685).

Runs the Phase 2 pipeline through the kicad adapter exactly as an agent calls
it -- generate_schematic (ERC + netlist match), generate_pcb, autoroute_pcb
(Freerouting, ground pour, DRC with schematic parity) -- then the fabrication
outputs and the 3D board, and copies the result into ``kicad/`` beside this
file with a ``report.json`` of every check. Run in the kicad-adapter image::

    python examples/drone_flight_controller/eda/generate.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from tool_registry.tools.kicad.adapter import KicadServer  # noqa: E402
from tool_registry.tools.kicad.config import KicadConfig  # noqa: E402

KEEP = (".kicad_sch", ".kicad_pcb", ".kicad_pro", "sym-lib-table", "fp-lib-table")


async def call(server: KicadServer, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tool/call",
        "params": {"tool_id": tool, "arguments": args},
    }
    resp = json.loads(await server.handle_request(json.dumps(req)))
    if "error" in resp:
        raise SystemExit(f"{tool}: {resp['error'].get('data') or resp['error']}")
    data: dict[str, Any] = resp["result"]["data"]
    return data


async def main() -> int:
    circuit = json.loads((HERE / "circuit.json").read_text(encoding="utf-8"))
    work = Path(os.environ.get("DRONE_FC_WORK") or tempfile.mkdtemp(prefix="drone-fc-"))
    server = KicadServer(KicadConfig(work_dir=str(work)))
    report: dict[str, Any] = {}

    sch = await call(server, "kicad.generate_schematic", {"circuit": circuit})
    report["schematic"] = {
        "verdict": sch["verdict"],
        "erc": sch["erc"],
        "netlist_mismatched": sch["netlist_check"]["mismatched"],
    }
    print("schematic", json.dumps(report["schematic"])[:600])
    if sch["verdict"] != "pass":
        return 1
    pcb = await call(server, "kicad.generate_pcb", {"circuit": circuit})
    report["pcb"] = {k: pcb[k] for k in ("verdict", "footprints", "unplaced")}
    print("pcb", report["pcb"])
    if pcb["verdict"] != "pass":
        return 1
    routed = await call(
        server, "kicad.autoroute_pcb", {"pcb_file": pcb["pcb_file"], "ground_pour": "GND"}
    )
    report["routing"] = {
        k: routed.get(k)
        for k in (
            "verdict",
            "unrouted",
            "tracks",
            "vias",
            "drc",
            "schematic_parity_checked",
            "schematic_parity",
        )
    }
    errs = [v for v in routed["drc"]["violations"] if v["severity"] == "error"]
    print(
        "routing",
        {k: routed.get(k) for k in ("verdict", "unrouted", "tracks", "vias")},
        "errors:",
        json.dumps(errs)[:1500],
        "parity:",
        routed.get("schematic_parity"),
    )
    if routed["verdict"] != "pass":
        return 1

    board = routed["pcb_file"]
    out = HERE / "kicad"
    out.mkdir(exist_ok=True)
    for f in Path(board).parent.iterdir():
        if f.is_file() and f.name.endswith(KEEP):
            shutil.copy2(f, out / f.name.replace(Path(board).stem, "drone-fc"))
    shutil.copy2(sch["schematic_file"], out / "drone-fc.kicad_sch")
    shutil.copy2(board, out / "drone-fc.kicad_pcb")

    step = await call(server, "kicad.export_3d", {"pcb_file": board, "format": "step"})
    report["step"] = {k: step[k] for k in ("complete", "missing_models", "models_referenced")}
    gerb = await call(server, "kicad.export_gerber", {"pcb_file": board})
    report["gerber_files"] = len(gerb["files_generated"])
    drill = await call(server, "kicad.export_drill", {"pcb_file": board})
    report["drill_holes"] = drill["total_holes"]
    bom = await call(server, "kicad.export_bom", {"schematic_file": sch["schematic_file"]})
    report["bom_components"] = bom["total_components"]
    render = await call(
        server,
        "kicad.render_pcb",
        {"pcb_file": board, "view": "top", "width": 1000, "height": 1000},
    )
    shutil.copy2(render["file"], out / "drone-fc-top.png")
    print(
        "outputs", {k: report[k] for k in ("step", "gerber_files", "drill_holes", "bom_components")}
    )
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
