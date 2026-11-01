"""3D board export, renders and component updates (FORGE-684).

``kicad.export_3d`` writes the board as STEP or GLB with kicad-cli, and says
which footprints had no 3D model: kicad-cli leaves them out silently, so the
adapter resolves each footprint's model path itself.

``kicad.render_pcb`` renders the board with kicad-cli's ray tracer (top,
bottom, or an isometric view) to PNG, for review.

``kicad.export_svg`` plots a schematic (one SVG per sheet) or a board side to SVG, for
previews (FORGE-687).

``kicad.update_components`` changes the value, footprint or fields of named
references in a schematic (and a board's value and fields), writing a new
revision in a new folder, then re-runs ERC. A footprint change makes the
board stale -- it needs regenerating from the schematic -- and is reported
as such rather than patched in place.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.sexpr import QStr

if TYPE_CHECKING:
    from tool_registry.tools.kicad.adapter import KicadServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.kicad.views")

_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")
#: kicad-cli rejects a leading '-' in --rotate's value; these are the same views.
VIEWS: dict[str, list[str]] = {
    "top": ["--side", "top"],
    "bottom": ["--side", "bottom"],
    "iso": ["--side", "top", "--perspective", "--rotate", "315,0,45", "--floor"],
}


def register_view_tools(server: KicadServer) -> None:
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.export_3d",
            adapter_id="kicad",
            name="Export 3D Board",
            description=(
                "Export the board as a 3D model: `format` step (CAD, for the enclosure) or "
                "glb (for viewing), with the components' 3D models from KiCad's library. "
                "kicad-cli leaves out a component whose model is missing without saying so; "
                "this lists them in `missing_models` and sets `complete` false."
            ),
            capability="pcb_3d_export",
            input_schema={
                "type": "object",
                "properties": {
                    "pcb_file": {"type": "string"},
                    "format": {"type": "string", "enum": ["step", "glb"], "default": "step"},
                },
                "required": ["pcb_file"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=2048, max_cpu_seconds=300),
        ),
        handler=lambda args: export_3d(server, args),
    )
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.render_pcb",
            adapter_id="kicad",
            name="Render PCB",
            description=(
                "Render the board in 3D to PNG with kicad-cli's ray tracer: `view` top, "
                "bottom or iso (perspective, with floor and shadows). Returns the image path "
                "and, with `inline` true, the PNG as base64 for showing to the person."
            ),
            capability="pcb_render",
            input_schema={
                "type": "object",
                "properties": {
                    "pcb_file": {"type": "string"},
                    "view": {"type": "string", "enum": list(VIEWS), "default": "top"},
                    "width": {"type": "integer", "minimum": 200, "maximum": 4000, "default": 1600},
                    "height": {"type": "integer", "minimum": 200, "maximum": 4000, "default": 1200},
                    "inline": {"type": "boolean", "default": False},
                },
                "required": ["pcb_file"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=2048, max_cpu_seconds=300),
        ),
        handler=lambda args: render_pcb(server, args),
    )
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.export_svg",
            adapter_id="kicad",
            name="Export SVG",
            description=(
                "Plot a schematic (one SVG per sheet) or a board (one SVG of `layers`, "
                "default the top side: copper, silkscreen, mask and outline, cropped to the "
                "board) with kicad-cli, for previews. Give `schematic_file` or `pcb_file`."
            ),
            capability="eda_svg_export",
            input_schema={
                "type": "object",
                "properties": {
                    "schematic_file": {"type": "string"},
                    "pcb_file": {"type": "string"},
                    "layers": {
                        "type": "string",
                        "description": (
                            "Board layers, comma separated (e.g. B.Cu,B.Silkscreen,Edge.Cuts)"
                        ),
                    },
                },
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
        ),
        handler=lambda args: export_svg(server, args),
    )
    server.register_tool(
        manifest=ToolManifest(
            tool_id="kicad.update_components",
            adapter_id="kicad",
            name="Update Components",
            description=(
                "Change the value, footprint or fields of named references (`changes`: "
                "[{ref, value?, footprint?, fields?}]) in a schematic, and with `pcb_file` "
                "the value and fields of the board's footprints, writing a new revision in a "
                "new folder. ERC then runs on the new schematic. A footprint change marks the "
                "board stale (regenerate it from the schematic). An unknown reference is "
                "refused by name."
            ),
            capability="component_update",
            input_schema={
                "type": "object",
                "properties": {
                    "schematic_file": {"type": "string"},
                    "pcb_file": {"type": "string"},
                    "changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ref": {"type": "string"},
                                "value": {"type": "string"},
                                "footprint": {"type": "string"},
                                "fields": {"type": "object"},
                            },
                            "required": ["ref"],
                        },
                    },
                },
                "required": ["schematic_file", "changes"],
            },
            output_schema={"type": "object"},
            phase=2,
            resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
        ),
        handler=lambda args: update_components(server, args),
    )


# -- export_3d ------------------------------------------------------------------------


def model_paths(pcb_text: str) -> list[tuple[str, str]]:
    """``(reference, model path)`` for every footprint model in a board file."""
    tree = sexpr.loads(pcb_text)
    out: list[tuple[str, str]] = []
    for fp in sexpr.children(tree, "footprint"):
        ref = next(
            (str(p[2]) for p in sexpr.children(fp, "property") if str(p[1]) == "Reference"), "?"
        )
        for model in sexpr.children(fp, "model"):
            hidden = sexpr.find(model, "hide")
            if hidden is not None and hidden[1:] in (["yes"], []):
                continue
            out.append((ref, str(model[1])))
    return out


def resolve_model(path: str, model_dir: str) -> Path:
    def var(m: re.Match[str]) -> str:
        name = m.group(1)
        if name.endswith("3DMODEL_DIR") or name == "KISYS3DMOD":
            return os.environ.get(name, model_dir)
        return os.environ.get(name, m.group(0))

    return Path(_VAR.sub(var, path))


def missing_models(pcb_file: str, model_dir: str) -> tuple[list[dict[str, str]], int]:
    """Footprints whose 3D model (STEP, or the VRML it substitutes) is not on disk."""
    paths = model_paths(Path(pcb_file).read_text(encoding="utf-8"))
    missing: list[dict[str, str]] = []
    for ref, raw in paths:
        p = resolve_model(raw, model_dir)
        candidates = [p, p.with_suffix(".step"), p.with_suffix(".stp")]
        if not any(c.is_file() for c in candidates):
            missing.append({"ref": ref, "model": raw})
    return missing, len(paths)


async def export_3d(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    pcb = server._existing(str(arguments.get("pcb_file") or ""), "board")
    fmt = str(arguments.get("format", "step")).lower()
    if fmt not in ("step", "glb"):
        raise ValueError("format must be step or glb")
    from tool_registry.tools.kicad.generation import _run

    out = Path(server.config.work_dir) / "3d" / f"{Path(pcb).stem}.{fmt}"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tracer.start_as_current_span("kicad.export_3d") as span:
        span.set_attribute("kicad.format", fmt)
        rc, stdout, err = await _run(
            [
                server.config.kicad_cli,
                "pcb",
                "export",
                fmt,
                "--subst-models",
                "-f",
                "-o",
                str(out),
                pcb,
            ],
            float(server.config.max_operation_time),
        )
        if rc != 0 or not out.is_file():
            raise ValueError(f"kicad-cli {fmt} export failed: {(err or stdout).strip()[-400:]}")
        missing, total = missing_models(pcb, server.config.model_dir)
        logger.info("board_3d_exported", fmt=fmt, models=total, missing=len(missing))
        return {
            "file": str(out),
            "format": fmt,
            "bytes": out.stat().st_size,
            "models_referenced": total,
            "missing_models": missing,
            "complete": not missing,
        }


# -- render ------------------------------------------------------------------------------


async def render_pcb(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    pcb = server._existing(str(arguments.get("pcb_file") or ""), "board")
    view = str(arguments.get("view", "top"))
    if view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}")
    width, height = int(arguments.get("width", 1600)), int(arguments.get("height", 1200))
    from tool_registry.tools.kicad.generation import _run

    out = Path(server.config.work_dir) / "renders" / f"{Path(pcb).stem}-{view}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tracer.start_as_current_span("kicad.render_pcb") as span:
        span.set_attribute("kicad.view", view)
        rc, stdout, err = await _run(
            [
                server.config.kicad_cli,
                "pcb",
                "render",
                *VIEWS[view],
                "--quality",
                "high",
                "--width",
                str(width),
                "--height",
                str(height),
                "-o",
                str(out),
                pcb,
            ],
            float(server.config.max_operation_time) * 2,
        )
        if rc != 0 or not out.is_file():
            raise ValueError(f"kicad-cli render failed: {(err or stdout).strip()[-400:]}")
        result: dict[str, Any] = {
            "file": str(out),
            "view": view,
            "width": width,
            "height": height,
            "bytes": out.stat().st_size,
        }
        if arguments.get("inline"):
            result["png_base64"] = base64.b64encode(out.read_bytes()).decode()
        return result


# -- svg ------------------------------------------------------------------------------------

#: The board's top side as a reviewer sees it (FORGE-687).
TOP_LAYERS = "F.Cu,F.Paste,F.Silkscreen,F.Mask,Edge.Cuts"


async def export_svg(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    sch, pcb = arguments.get("schematic_file"), arguments.get("pcb_file")
    if bool(sch) == bool(pcb):
        raise ValueError("give exactly one of schematic_file or pcb_file")
    from tool_registry.tools.kicad.generation import _run

    cli, timeout = server.config.kicad_cli, float(server.config.max_operation_time)
    with tracer.start_as_current_span("kicad.export_svg") as span:
        if sch:
            src = server._existing(str(sch), "schematic")
            out_dir = Path(server.config.work_dir) / "svg" / f"{Path(src).stem}-sch"
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True)
            cmd = [cli, "sch", "export", "svg", "--exclude-drawing-sheet", "-o", str(out_dir), src]
            rc, stdout, err = await _run(cmd, timeout)
            files = sorted(out_dir.glob("*.svg"))
            kind, layers = "schematic", ""
        else:
            src = server._existing(str(pcb), "board")
            layers = str(arguments.get("layers") or TOP_LAYERS)
            out = Path(server.config.work_dir) / "svg" / f"{Path(src).stem}-pcb.svg"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.unlink(missing_ok=True)
            cmd = [
                cli,
                "pcb",
                "export",
                "svg",
                "--layers",
                layers,
                "--mode-single",
                "--page-size-mode",
                "2",
                "--exclude-drawing-sheet",
                "--drill-shape-opt",
                "2",
                "-o",
                str(out),
                src,
            ]
            rc, stdout, err = await _run(cmd, timeout)
            files = [out] if out.is_file() else []
            kind = "board"
        span.set_attribute("kicad.svg.kind", kind)
        span.set_attribute("kicad.svg.files", len(files))
        if rc != 0 or not files:
            raise ValueError(f"kicad-cli svg export failed: {(err or stdout).strip()[-400:]}")
        logger.info("svg_exported", kind=kind, files=len(files))
        result: dict[str, Any] = {
            "kind": kind,
            "files": [{"file": str(f), "bytes": f.stat().st_size} for f in files],
        }
        if layers:
            result["layers"] = layers.split(",")
        return result


# -- update_components --------------------------------------------------------------------


def _set_property(item: list[Any], name: str, val: str, hide: bool) -> bool:
    """Set ``(property name val ...)`` on a symbol/footprint; add it if absent."""
    for prop in sexpr.children(item, "property"):
        if str(prop[1]) == name:
            prop[2] = QStr(val)
            return False
    at = sexpr.find(item, "at") or ["at", "0", "0", "0"]
    effects: list[Any] = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if hide:
        effects.append(["hide", "yes"])
    item.append(["property", QStr(name), QStr(val), ["at", *at[1:3], "0"], effects])
    return True


def apply_schematic_changes(sch_text: str, changes: list[dict[str, Any]]) -> tuple[str, list[str]]:
    tree = sexpr.loads(sch_text)
    by_ref: dict[str, list[list[Any]]] = {}
    for sym in sexpr.children(tree, "symbol"):
        ref = next(
            (str(p[2]) for p in sexpr.children(sym, "property") if str(p[1]) == "Reference"), ""
        )
        if ref:
            by_ref.setdefault(ref, []).append(sym)
    unknown = [c["ref"] for c in changes if c["ref"] not in by_ref]
    if unknown:
        raise ValueError(f"no such reference in the schematic: {', '.join(unknown)}")
    done: list[str] = []
    for change in changes:
        for sym in by_ref[change["ref"]]:  # every unit of a multi-unit part
            if "value" in change:
                _set_property(sym, "Value", str(change["value"]), hide=False)
            if "footprint" in change:
                _set_property(sym, "Footprint", str(change["footprint"]), hide=True)
            for k, v in (change.get("fields") or {}).items():
                _set_property(sym, str(k), str(v), hide=True)
        done.append(change["ref"])
    return sexpr.dumps(tree) + "\n", done


def apply_board_changes(pcb_text: str, changes: list[dict[str, Any]]) -> str:
    tree = sexpr.loads(pcb_text)
    for fp in sexpr.children(tree, "footprint"):
        ref = next(
            (str(p[2]) for p in sexpr.children(fp, "property") if str(p[1]) == "Reference"), ""
        )
        for change in changes:
            if change["ref"] != ref:
                continue
            if "value" in change:
                _set_property(fp, "Value", str(change["value"]), hide=False)
            for k, v in (change.get("fields") or {}).items():
                _set_property(fp, str(k), str(v), hide=True)
    return sexpr.dumps(tree) + "\n"


async def update_components(server: KicadServer, arguments: dict[str, Any]) -> dict[str, Any]:
    sch = Path(server._existing(str(arguments.get("schematic_file") or ""), "schematic"))
    changes = list(arguments.get("changes") or [])
    if not changes or any(not isinstance(c, dict) or not c.get("ref") for c in changes):
        raise ValueError("changes must be a non-empty list of {ref, value?, footprint?, fields?}")
    pcb_arg = arguments.get("pcb_file")
    pcb = Path(server._existing(str(pcb_arg), "board")) if pcb_arg else None
    from tool_registry.tools.kicad.generation import _copy_project, _new_folder

    with tracer.start_as_current_span("kicad.update_components"):
        folder = _new_folder(server, f"{sch.stem}-rev")
        _copy_project(sch, folder)
        new_sch = folder / sch.name
        text, done = apply_schematic_changes(sch.read_text(encoding="utf-8"), changes)
        new_sch.write_text(text, encoding="utf-8")
        new_pcb = None
        if pcb is not None:
            new_pcb = folder / pcb.name
            if not new_pcb.exists():
                shutil.copy2(pcb, new_pcb)
            new_pcb.write_text(
                apply_board_changes(new_pcb.read_text(encoding="utf-8"), changes),
                encoding="utf-8",
            )
        erc = await server._execute_erc(str(new_sch), "all")
        stale = [c["ref"] for c in changes if "footprint" in c]
        logger.info("components_updated", refs=done, board_stale=bool(stale))
        return {
            "schematic_file": str(new_sch),
            "pcb_file": str(new_pcb) if new_pcb else None,
            "updated": done,
            "erc": {k: erc.get(k) for k in ("errors", "warnings", "passed")},
            "board_stale": bool(stale and new_pcb is not None),
            "footprint_changes": stale,
            "note": (
                "footprints changed: regenerate the board from the new schematic "
                "(kicad.generate_pcb) and route it again"
                if stale
                else ""
            ),
        }
