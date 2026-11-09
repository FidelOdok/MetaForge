"""Read installed KiCad library metadata without inventing package associations."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any

from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.schematic_gen import SymbolLibrary, symbol_pins


def _under(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise ValueError("library reference is outside the configured root")
    return target


def describe(config: Any, symbol_id: str) -> dict[str, Any]:
    library, name = symbol_id.split(":", 1)
    source = _under(Path(config.symbol_dir), library + ".kicad_sym")
    symbol = SymbolLibrary(config.symbol_dir).symbol(symbol_id)
    properties = {str(p[1]): str(p[2]) for p in sexpr.children(symbol, "property") if len(p) > 2}
    footprint = properties.get("Footprint", "")
    models = []
    footprint_path = None
    if footprint:
        lib, part = footprint.split(":", 1)
        footprint_path = _under(Path(config.footprint_dir), f"{lib}.pretty/{part}.kicad_mod")
        if footprint_path.is_file():
            tree = sexpr.loads(footprint_path.read_text())
            for model in sexpr.children(tree, "model"):
                original = str(model[1])
                resolved = re.sub(r"\$\{KICAD\d+_3DMODEL_DIR\}", config.model_dir, original)
                if resolved == original and "${" in original:
                    models.append(
                        {
                            "reference": original,
                            "available": False,
                            "reason": "unresolved path variable",
                        }
                    )
                    continue
                path = Path(resolved)
                if not path.is_absolute():
                    path = Path(config.model_dir) / path
                path = _under(Path(config.model_dir), str(path.relative_to(config.model_dir)))
                models.append(
                    {"reference": original, "path": str(path), "available": path.is_file()}
                )
    return {
        "symbol_id": symbol_id,
        "properties": properties,
        "pins": [
            {"number": p.number, "name": p.name, "type": p.etype, "unit": p.unit}
            for p in symbol_pins(symbol)
        ],
        "source": {
            "kind": "installed_kicad_library",
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "footprint": footprint,
        "footprint_available": bool(footprint_path and footprint_path.is_file()),
        "models": models,
        "limitations": [
            "Symbol identity is not proof of an exact purchasable part "
            "or an executable simulation model."
        ],
    }


def search(config: Any, query: str, limit: int = 10) -> dict[str, Any]:
    if not query.strip():
        raise ValueError("query is required")
    if not Path(config.symbol_dir).is_dir():
        raise ValueError("KiCad symbol library directory is not installed")
    terms = query.lower().split()
    results = []
    for source in sorted(Path(config.symbol_dir).glob("*.kicad_sym")):
        tree = sexpr.loads(source.read_text())
        for symbol in sexpr.children(tree, "symbol"):
            identity = f"{source.stem}:{symbol[1]}"
            props = {str(p[1]): str(p[2]) for p in sexpr.children(symbol, "property") if len(p) > 2}
            haystack = (
                f"{identity} {props.get('Description', '')} {props.get('ki_keywords', '')}".lower()
            )
            if all(term in haystack for term in terms):
                results.append(
                    {
                        "symbol_id": identity,
                        "description": props.get("Description", ""),
                        "footprint": props.get("Footprint", ""),
                    }
                )
                if len(results) >= limit:
                    return {"results": results, "limited": True}
    return {"results": results, "limited": False}


def register_library_tools(server: Any) -> None:
    async def search_handler(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 10))
        if not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")
        return await asyncio.to_thread(search, server.config, str(args.get("query", "")), limit)

    async def describe_handler(args: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(describe, server.config, str(args["symbol_id"]))

    for name, handler, properties, required, description in (
        (
            "search_library",
            search_handler,
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            ["query"],
            "Search installed KiCad symbols before proposing a new component.",
        ),
        (
            "describe_library_component",
            describe_handler,
            {"symbol_id": {"type": "string"}},
            ["symbol_id"],
            "Read inherited symbol pins, footprint and available 3D references with source hashes.",
        ),
    ):
        server.register_tool(
            manifest=ToolManifest(
                tool_id=f"kicad.{name}",
                adapter_id="kicad",
                name=name,
                description=description,
                capability="component_library",
                input_schema={"type": "object", "properties": properties, "required": required},
                output_schema={"type": "object"},
                phase=1,
                chat_visible=False,
                chat_discoverable=True,
                resource_limits=ResourceLimits(max_memory_mb=512, max_cpu_seconds=30),
            ),
            handler=handler,
        )
