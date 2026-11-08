"""Phase 2 KiCad write tools: schematic, board and routing from a circuit (FORGE-683).

Registered on the KiCad adapter by :func:`register_generation_tools`. Each
tool writes new files in a fresh folder of the adapter workspace and never
touches a file the caller passed in; each result is judged by KiCad's own
checks, not by this code:

- ``kicad.generate_schematic`` -- then ERC, and the netlist KiCad exports from
  it compared net by net with the circuit;
- ``kicad.generate_pcb`` -- footprints, nets, outline, rules and placement
  (pcbnew, in a worker under the system Python), honouring each component's
  ``placement`` (position, rotation, side, edge mount) and the board's
  ``mounting_holes``;
- ``kicad.edit_pcb`` -- an existing board changed by an ordered list of
  operations (move, rotate, place, mounting holes, strip routing) in a new
  folder, then re-routed as ``kicad.autoroute_pcb`` routes;
- ``kicad.autoroute_pcb`` -- Freerouting on a copy, then DRC with schematic
  parity: unrouted connections, errors and mismatches are reported, and the
  verdict is ``pass`` only when all three are zero.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.circuit import Circuit
from tool_registry.tools.kicad.schematic_gen import (
    SymbolLibrary,
    SymbolNotFoundError,
    generate_schematic,
    write_project_files,
)

if TYPE_CHECKING:
    from tool_registry.tools.kicad.adapter import KicadServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.kicad.generation")

WORKER = Path(__file__).with_name("pcb_worker.py")
_PROJECT_SUFFIXES = (".kicad_sch", ".kicad_pro", ".kicad_prl")
_PROJECT_FILES = ("sym-lib-table", "fp-lib-table")


class GenerationError(RuntimeError):
    """A generation step failed; the message names what and why."""


def register_generation_tools(server: KicadServer) -> None:
    circuit_schema = _inline(Circuit.model_json_schema())
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.generate_schematic",
            adapter_id="kicad",
            name="Generate Schematic",
            description=(
                "Generate a KiCad 9 schematic from a circuit description: components "
                "(ref, KiCad symbol lib_id, footprint lib_id, value, fields) and nets "
                "(name, nodes as REF.PIN). Symbols come from the installed KiCad libraries; "
                "pins are joined by net labels on their connection points, unused pins get "
                "no-connect flags, undriven power nets a PWR_FLAG. Then KiCad's ERC runs on "
                "it and the netlist KiCad exports is compared net by net with the circuit. "
                "verdict pass = no ERC error and every net matched. Writes a new project "
                "folder; an unknown symbol or pin is refused by name. A part drawn with "
                "a symbol whose name is not its MPN (a stand-in, e.g. BMI160 for a "
                "BMI270) is listed in symbol_substitutions with a warning until the "
                "component's symbol_pinout_checked says how its pins were checked "
                "against the datasheet; unconfirmed_symbol_substitutions counts them."
            ),
            capability="schematic_generation",
            input_schema={
                "type": "object",
                "properties": {"circuit": circuit_schema},
                "required": ["circuit"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=180),
        ),
        handler=lambda args: generate_schematic_tool(server, args),
    )
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.generate_pcb",
            adapter_id="kicad",
            name="Generate PCB",
            description=(
                "Generate a KiCad 9 board from a circuit with a `board` (width_mm, "
                "height_mm, layers, clearance_mm, track_mm, power nets and track width, "
                "via sizes, ground_pour net, mounting_holes_mm). Footprints come from the "
                "installed KiCad libraries with their pads on the circuit's nets; the "
                "outline, design rules and net classes are set. A component's `placement` "
                "is honoured: `position` {x_mm, y_mm} from the board's top-left corner (x "
                "right, y DOWN), `rotation_deg` (KiCad, counter-clockwise), `side` "
                "top|bottom, and for a connector `edge_mount` {edge: +x|-x|+y|-y, "
                "overhang_mm}: the footprint is turned so its mating side faces that edge "
                "(from the footprint's PCB Edge marker, else its pad and body geometry) and "
                "its mouth sits overhang_mm beyond the edge (0 flush). `board."
                "mounting_holes` [{position, drill_mm}] puts holes where given instead of "
                "the four corners. Every other part is auto-placed: connectors on the left "
                "edge, the rest next to their best-connected neighbour (a deterministic "
                "starting point for review, not an optimal placement). The schematic is "
                "generated alongside (or `schematic_file` given) and each footprint is "
                "linked to its symbol. Unplaceable parts are listed; placements report "
                "board_x_mm, board_y_mm, rotation_deg, side and, for an edge mount, the "
                "measured overhang. To change a board afterwards use kicad.edit_pcb."
            ),
            capability="pcb_generation",
            input_schema={
                "type": "object",
                "properties": {
                    "circuit": circuit_schema,
                    "schematic_file": {
                        "type": "string",
                        "description": "A schematic generated from this circuit, to link to",
                    },
                },
                "required": ["circuit"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=2048, max_cpu_seconds=300),
        ),
        handler=lambda args: generate_pcb_tool(server, args),
    )
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.edit_pcb",
            adapter_id="kicad",
            name="Edit PCB",
            description=(
                "Change an existing board without uploading a file: the board (and its "
                "project, so schematic parity still runs) is copied to a new folder of the "
                "adapter workspace, an ordered list of `operations` is applied, and the "
                "result is re-routed with Freerouting and checked by DRC as "
                "kicad.autoroute_pcb does. Operations (positions in mm from the board's "
                "top-left corner, x right, y DOWN): {op: move, ref, position, "
                "rotation_deg?}; {op: rotate, ref, rotation_deg}; {op: place, ref, "
                "position?, rotation_deg?, side?, edge_mount?: {edge: +x|-x|+y|-y, "
                "overhang_mm}} (the same rules as a generate_pcb placement); {op: "
                "set_mounting_hole, ref?, position, drill_mm?} (moves H<n>, or adds one); "
                "{op: remove_mounting_hole, ref}; {op: strip_routing}. With reroute "
                "(default true) the old tracks, vias and outer pours are stripped first "
                "and the ground pour net is reused. The input file is never changed. "
                "Record the new board with twin.record_design_files (work_product_type "
                "pcb_layout, the board's item_key) to make it the next PCB revision."
            ),
            capability="pcb_generation",
            input_schema={
                "type": "object",
                "properties": {
                    "pcb_file": {"type": "string", "description": "Board to edit (copied)"},
                    "operations": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "move",
                                        "rotate",
                                        "place",
                                        "set_mounting_hole",
                                        "remove_mounting_hole",
                                        "strip_routing",
                                    ],
                                },
                                "ref": {"type": "string"},
                                "position": {
                                    "type": "object",
                                    "properties": {
                                        "x_mm": {"type": "number"},
                                        "y_mm": {"type": "number"},
                                    },
                                    "required": ["x_mm", "y_mm"],
                                },
                                "rotation_deg": {"type": "number"},
                                "side": {"type": "string", "enum": ["top", "bottom"]},
                                "edge_mount": {
                                    "type": "object",
                                    "properties": {
                                        "edge": {
                                            "type": "string",
                                            "enum": ["+x", "-x", "+y", "-y"],
                                        },
                                        "overhang_mm": {"type": "number"},
                                    },
                                    "required": ["edge"],
                                },
                                "drill_mm": {"type": "number", "exclusiveMinimum": 0},
                            },
                            "required": ["op"],
                        },
                    },
                    "reroute": {"type": "boolean", "default": True},
                    "passes": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
                    "ground_pour": {
                        "type": "string",
                        "description": "Net to pour after routing; default: the pour it had",
                    },
                },
                "required": ["pcb_file", "operations"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=4096, max_cpu_seconds=900),
        ),
        handler=lambda args: edit_pcb_tool(server, args),
    )
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.autoroute_pcb",
            adapter_id="kicad",
            name="Autoroute PCB",
            description=(
                "Route a board with Freerouting (a separate GPL-3 program) through "
                "KiCad's Specctra export and import, on a copy in a new folder, then pour "
                "and fill `ground_pour`, then run KiCad's DRC with schematic parity when "
                "the schematic sits beside the board. Returns tracks, vias, track length, "
                "the unrouted connections, DRC errors and warnings, and parity issues; "
                "verdict pass only when unrouted, DRC errors and parity issues are all zero."
            ),
            capability="pcb_autoroute",
            input_schema={
                "type": "object",
                "properties": {
                    "pcb_file": {"type": "string", "description": "Board to route (copied)"},
                    "passes": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
                    "ground_pour": {"type": "string", "description": "Net to pour, e.g. GND"},
                },
                "required": ["pcb_file"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=4096, max_cpu_seconds=900),
        ),
        handler=lambda args: autoroute_pcb_tool(server, args),
    )


# -- tools ---------------------------------------------------------------------------


async def generate_schematic_tool(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    circuit = Circuit.model_validate(arguments.get("circuit") or {})
    folder = _new_folder(server, circuit.name)
    with tracer.start_as_current_span("kicad.generate_schematic") as span:
        span.set_attribute("kicad.components", len(circuit.components))
        return await _schematic(server, circuit, folder)


async def _schematic(server: KicadServer, circuit: Circuit, folder: Path) -> dict[str, Any]:
    path = folder / f"{circuit.name}.kicad_sch"
    try:
        gen = generate_schematic(circuit, str(path), SymbolLibrary(server.config.symbol_dir))
    except SymbolNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    erc = await server._execute_erc(str(path), "all")
    check = await _netlist_check(server, circuit, path)
    passed = erc.get("errors", 0) == 0 and not check["mismatched"]
    unconfirmed = [s for s in gen.substitutions if not s["confirmed"]]
    logger.info(
        "schematic_generated",
        circuit=circuit.name,
        symbols=gen.symbols_placed,
        erc_errors=erc.get("errors"),
        mismatched=len(check["mismatched"]),
        symbol_substitutions=len(gen.substitutions),
        unconfirmed_substitutions=len(unconfirmed),
    )
    return {
        "verdict": "pass" if passed else "fail",
        "schematic_file": str(path),
        "project_dir": str(folder),
        "symbols_placed": gen.symbols_placed,
        "labels": gen.labels,
        "no_connects": gen.no_connects,
        "power_flags": gen.power_flags,
        "paper": gen.paper,
        "erc": {k: erc.get(k) for k in ("errors", "warnings", "violations", "passed")},
        "netlist_check": check,
        "pins": gen.pins,
        "symbol_uuids": gen.symbol_uuids,
        # FORGE-699: a part drawn with another part's symbol. ERC and the netlist
        # check pass on the stand-in's pins, so only a person can confirm them.
        "symbol_substitutions": gen.substitutions,
        "unconfirmed_symbol_substitutions": len(unconfirmed),
        "warnings": [s["warning"] for s in unconfirmed],
    }


async def generate_pcb_tool(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    circuit = Circuit.model_validate(arguments.get("circuit") or {})
    if circuit.board is None:
        raise ValueError("circuit.board is required to generate a board (width_mm, height_mm)")
    with tracer.start_as_current_span("kicad.generate_pcb") as span:
        span.set_attribute("kicad.components", len(circuit.components))
        sch_given = arguments.get("schematic_file")
        if sch_given:
            sch_path = Path(server._existing(str(sch_given), "schematic"))
            folder = _new_folder(server, circuit.name)
            _copy_project(sch_path, folder)
            sch_path = folder / sch_path.name
            uuids = _symbol_uuids(sch_path)
            schematic = None
        else:
            folder = _new_folder(server, circuit.name)
            schematic = await _schematic(server, circuit, folder)
            sch_path = Path(schematic["schematic_file"])
            uuids = schematic["symbol_uuids"]
        write_project_files(
            folder,
            circuit.name,
            symbol_libs=set(),
            footprint_libs={c.footprint.split(":", 1)[0] for c in circuit.components},
            symbol_root=server.config.symbol_dir,
            footprint_root=server.config.footprint_dir,
        ) if not (folder / "fp-lib-table").exists() else None
        pcb_path = folder / f"{sch_path.stem}.kicad_pcb"
        # Pads take their nets from the netlist KiCad exports from the schematic,
        # as KiCad's own update-from-schematic does: that includes the
        # unconnected-(...) net KiCad gives each no-connect pin, which
        # schematic parity in DRC otherwise reports as missing.
        pad_nets = await _schematic_pad_nets(server, sch_path)
        result = await _worker(
            server,
            {
                "action": "generate",
                "circuit": circuit.model_dump(),
                "pad_nets": pad_nets,
                "out_path": str(pcb_path),
                "symbol_uuids": uuids,
                "sheet_file": sch_path.name,
                "footprint_root": server.config.footprint_dir,
            },
        )
        if not result.get("ok"):
            raise ValueError("; ".join(result.get("errors") or ["board generation failed"]))
        out: dict[str, Any] = {
            "verdict": "pass" if not result["unplaced"] else "fail",
            "pcb_file": str(pcb_path),
            "schematic_file": str(sch_path),
            "project_dir": str(folder),
            "footprints": result["footprints"],
            "nets": result["nets"],
            "placements": result["placements"],
            "unplaced": result["unplaced"],
            "placement_warnings": result.get("placement_warnings", []),
        }
        if schematic is not None:
            out["schematic"] = {
                k: schematic[k]
                for k in (
                    "verdict",
                    "erc",
                    "netlist_check",
                    "symbol_substitutions",
                    "unconfirmed_symbol_substitutions",
                    "warnings",
                )
            }
        fixed = sum(1 for p in result["placements"] if p.get("fixed"))
        span.set_attribute("kicad.placements.fixed", fixed)
        logger.info(
            "pcb_generated",
            circuit=circuit.name,
            unplaced=len(result["unplaced"]),
            fixed_placements=fixed,
            placement_warnings=len(out["placement_warnings"]),
        )
        return out


async def autoroute_pcb_tool(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    src = Path(server._existing(str(arguments.get("pcb_file") or ""), "board"))
    passes = _passes(arguments)
    with tracer.start_as_current_span("kicad.autoroute_pcb") as span:
        folder = _new_folder(server, f"{src.stem}-routed")
        _copy_project(src, folder)
        result = await _route(server, folder / src.name, passes, arguments.get("ground_pour"))
        span.set_attribute("kicad.route.unrouted", int(result.get("unrouted") or 0))
        return result


async def edit_pcb_tool(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    src = Path(server._existing(str(arguments.get("pcb_file") or ""), "board"))
    operations = arguments.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations must be a non-empty list")
    reroute = bool(arguments.get("reroute", True))
    passes = _passes(arguments)
    with tracer.start_as_current_span("kicad.edit_pcb") as span:
        span.set_attribute("kicad.edit.operations", len(operations))
        folder = _new_folder(server, f"{src.stem}-edited")
        _copy_project(src, folder)
        board = folder / src.name
        edited = await _worker(
            server,
            {
                "action": "edit",
                "pcb_path": str(board),
                "operations": operations,
                "strip_routing": reroute,
                "footprint_root": server.config.footprint_dir,
            },
        )
        if not edited.get("ok"):
            raise ValueError("; ".join(edited.get("errors") or ["board edit failed"]))
        out: dict[str, Any] = {
            "pcb_file": str(board),
            "project_dir": str(folder),
            "source_pcb_file": str(src),
            "applied": edited["applied"],
            "placements": edited["placements"],
            "overlaps": edited["overlaps"],
            "beyond_board_edge": edited["beyond_board_edge"],
            "routing_stripped": edited["routing_stripped"],
            "routed": None,
            "next": (
                "record this board with twin.record_design_files (work_product_type "
                "pcb_layout, file_path pcb_file, the board's item_key) so it becomes the "
                "next PCB revision"
            ),
        }
        if reroute:
            stripped = edited.get("routing_stripped") or {}
            pour = arguments.get("ground_pour") or next(iter(stripped.get("pour_nets") or []), None)
            out["routed"] = await _route(server, board, passes, pour)
            out["verdict"] = out["routed"]["verdict"]
        else:
            out["verdict"] = "pass" if not edited["overlaps"] else "fail"
        span.set_attribute("kicad.edit.overlaps", len(edited["overlaps"]))
        logger.info(
            "pcb_edited",
            board=str(board),
            source=str(src),
            operations=len(operations),
            overlaps=len(edited["overlaps"]),
            rerouted=reroute,
            verdict=out["verdict"],
        )
        return out


def _passes(arguments: dict[str, Any]) -> int:
    passes = int(arguments.get("passes", 30))
    if not 1 <= passes <= 200:
        raise ValueError("passes must be 1..200")
    return passes


async def _route(
    server: KicadServer, board: Path, passes: int, ground_pour: str | None
) -> dict[str, Any]:
    """Route ``board`` in place with Freerouting, then DRC with parity when it can."""
    folder = board.parent
    routed = await _worker(
        server,
        {
            "action": "route",
            "pcb_path": str(board),
            "router_cmd": server.config.router_cmd,
            "passes": passes,
            "ground_pour": ground_pour,
            "timeout_s": server.config.max_route_time,
        },
        timeout=server.config.max_route_time + 120,
    )
    if not routed.get("ok"):
        return {
            "verdict": "not_established",
            "pcb_file": str(board),
            "reason": "; ".join(routed.get("errors") or ["routing failed"]),
            "log_tail": routed.get("log_tail", ""),
        }
    parity = (folder / f"{board.stem}.kicad_sch").exists()
    drc = await server._execute_drc(str(board), "all", None, schematic_parity=parity)
    unrouted = int(drc.get("unconnected_items", 0))
    issues = drc.get("schematic_parity", [])
    verdict = "pass" if (unrouted == 0 and drc.get("errors", 0) == 0 and not issues) else "fail"
    logger.info("pcb_routed", board=str(board), unrouted=unrouted, verdict=verdict)
    return {
        "verdict": verdict,
        "pcb_file": str(board),
        "project_dir": str(folder),
        "tracks": routed["tracks"],
        "vias": routed["vias"],
        "track_length_mm": routed["track_length_mm"],
        "router_time_s": routed["router_time_s"],
        "unrouted": unrouted,
        "drc": {k: drc.get(k) for k in ("errors", "warnings", "violations")},
        "schematic_parity_checked": parity,
        "schematic_parity": issues,
    }


# -- helpers ------------------------------------------------------------------------


def _new_folder(server: KicadServer, name: str) -> Path:
    folder = Path(server.config.work_dir) / "generated" / f"{name}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _copy_project(anchor: Path, folder: Path) -> None:
    """Copy the project a file belongs to (same stem, its tables) into ``folder``."""
    for sibling in anchor.parent.iterdir():
        same_stem = sibling.stem == anchor.stem and sibling.suffix in (
            *_PROJECT_SUFFIXES,
            ".kicad_pcb",
        )
        if (same_stem or sibling.name in _PROJECT_FILES) and sibling.is_file():
            shutil.copy2(sibling, folder / sibling.name)


def _symbol_uuids(sch_path: Path) -> dict[str, str]:
    """Reference -> symbol uuid (unit 1) read from a schematic."""
    tree = sexpr.loads(sch_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for sym in sexpr.children(tree, "symbol"):
        ref = next(
            (str(p[2]) for p in sexpr.children(sym, "property") if str(p[1]) == "Reference"), ""
        )
        if ref and not ref.startswith("#") and sexpr.value(sym, "unit", "1") == "1":
            out[ref] = sexpr.value(sym, "uuid")
    return out


async def _export_nets(server: KicadServer, sch: Path) -> dict[str, set[str]] | str:
    """Net name -> {REF.PIN} from KiCad's own netlist of ``sch``, or the error."""
    net_file = sch.with_suffix(".net")
    rc, _out, err = await _run(
        [
            server.config.kicad_cli,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "--output",
            str(net_file),
            str(sch),
        ],
        float(server.config.max_operation_time),
    )
    if rc != 0 or not net_file.exists():
        return f"netlist export failed: {err.strip()[-300:]}"
    tree = sexpr.loads(net_file.read_text(encoding="utf-8"))
    nets_node = sexpr.find(tree, "nets")
    got: dict[str, set[str]] = {}
    for net in sexpr.children(nets_node, "net") if nets_node else []:
        got[sexpr.value(net, "name").lstrip("/")] = {
            f"{sexpr.value(n, 'ref')}.{sexpr.value(n, 'pin')}" for n in sexpr.children(net, "node")
        }
    return got


async def _schematic_pad_nets(server: KicadServer, sch: Path) -> dict[str, str]:
    nets = await _export_nets(server, sch)
    if isinstance(nets, str):
        raise GenerationError(nets)
    return {node: name for name, nodes in nets.items() for node in nodes}


async def _netlist_check(server: KicadServer, circuit: Circuit, sch: Path) -> dict[str, Any]:
    """KiCad's own netlist of the schematic against the circuit, net by net."""
    got = await _export_nets(server, sch)
    if isinstance(got, str):
        return {"matched": [], "mismatched": [{"net": "*", "reason": got}]}
    matched, mismatched = [], []
    for cnet in circuit.nets:
        want = set(cnet.nodes)
        have = got.get(cnet.name)
        if have == want:
            matched.append(cnet.name)
        else:
            mismatched.append(
                {
                    "net": cnet.name,
                    "missing": sorted(want - (have or set())),
                    "extra": sorted((have or set()) - want),
                }
            )
    return {"matched": matched, "mismatched": mismatched}


async def _worker(
    server: KicadServer, request: dict[str, Any], timeout: float | None = None
) -> dict[str, Any]:
    rc, out, err = await _run(
        [server.config.pcbnew_python, str(WORKER)],
        timeout or float(server.config.max_operation_time) * 3,
        stdin=json.dumps(request),
    )
    marker = "@@RESULT@@"
    if marker not in out:
        raise GenerationError(f"pcb worker failed (exit {rc}): {(err or out)[-800:]}")
    result: dict[str, Any] = json.loads(out.rsplit(marker, 1)[1])
    return result


async def _run(cmd: list[str], timeout: float, stdin: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise GenerationError(f"{Path(cmd[0]).name} exceeded {timeout:.0f}s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(dict(defs[ref.split("/")[-1]]))
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    result: dict[str, Any] = inline(schema)
    return result
