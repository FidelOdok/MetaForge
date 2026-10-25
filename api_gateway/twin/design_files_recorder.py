"""Electronics and firmware design files as twin work products (FORGE-687).

``twin.record_design_files`` records a file or a folder the KiCad or firmware
adapter wrote on the shared workspace as one work product:

========================  ==============================================
``schematic``             a KiCad project's ``.kicad_sch`` sheets
``pcb_layout``            its ``.kicad_pcb`` board
``gerber``                a fabrication set: copper, mask, silk, outline
                          gerbers and the drill files
``pick_and_place``        the placement file (``kicad.export_pos``)
``pinmap``                a pin map (CSV or JSON)
``firmware_source``       a PlatformIO project's sources, and its build
                          output (``firmware.elf/.bin/.hex``) when present
========================  ==============================================

Each is a versioned definition (registry rows ``SCH``/``PCB``/``GBR``/``PNP``/
``PIN``/``FW``): recording the same name again is that item's next revision.
The primary file is the node's blob; every file is also a named blob under
``metadata.design_files`` (relative path -> object key), served by
``GET /v1/twin/nodes/{id}/files/{path}``. The node is linked
``GENERATED_FROM`` each source node (the BOM a schematic came from, the
schematic a board came from, the board its gerbers came from), and to its
project under its own type, which is what a flow's ``records:`` counts.

A schematic or board also gets an SVG preview from ``kicad.export_svg`` (one
per sheet, or the board's top side), under ``metadata.image_files``. The
preview is best effort: if KiCad is not reachable the files are recorded
without it, and ``preview_error`` says why.

Files are only read from inside the adapter workspace; a path that resolves
outside it is refused.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.twin.item_revisions import (
    finish_definition_revision,
    plan_definition_revision,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.design_files_recorder")

#: A folder may not hold more than this many files, or this many bytes.
MAX_FILES = 300
MAX_BYTES = 40 * 1024 * 1024

_GERBER_SUFFIX = re.compile(r"^\.(gbr|gbrjob|drl|xln|g[a-z0-9]{1,3})$", re.IGNORECASE)
_SOURCE_SUFFIXES = {
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".hh",
    ".ino",
    ".s",
    ".ld",
    ".ini",
    ".txt",
    ".json",
    ".md",
    ".cmake",
    ".yaml",
    ".yml",
    ".csv",
}
_SOURCE_DIRS = ("src", "include", "lib", "test", "boards")
_BUILD_OUTPUTS = ("firmware.elf", "firmware.bin", "firmware.hex", "firmware.map")
_SKIP_DIRS = {".pio", ".git", "build", "__pycache__", ".vscode", "node_modules"}

#: KiCad's gerber name suffixes (``<board>-F_Cu.gtl``) -> what the layer is.
_LAYER_WORDS = (
    ("Edge_Cuts", "outline"),
    ("_Cu", "copper"),
    ("_Mask", "soldermask"),
    ("_Silkscreen", "silkscreen"),
    ("_SilkS", "silkscreen"),
    ("_Paste", "solderpaste"),
    ("-job", "job"),
)


@dataclass(frozen=True)
class _Kind:
    wp_type: str
    domain: str
    fmt: str
    author: str


KINDS: dict[str, _Kind] = {
    "schematic": _Kind("schematic", "electronics", "kicad_sch", "electronics.design"),
    "pcb_layout": _Kind("pcb_layout", "electronics", "kicad_pcb", "electronics.design"),
    "gerber": _Kind("gerber", "electronics", "gbr", "electronics.design"),
    "pick_and_place": _Kind("pick_and_place", "electronics", "csv", "electronics.design"),
    "pinmap": _Kind("pinmap", "firmware", "csv", "firmware.design"),
    "firmware_source": _Kind("firmware_source", "firmware", "c", "firmware.design"),
}


def workspace_root() -> Path:
    return Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))


def resolve_in_workspace(file_path: str, root: Path | None = None) -> Path:
    """``file_path`` (absolute, or relative to the workspace) inside the workspace."""
    base = (root or workspace_root()).resolve()
    path = Path(file_path)
    resolved = (path if path.is_absolute() else base / path).resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"'{file_path}' is outside the adapter workspace ({base})")
    if not resolved.exists():
        raise ValueError(f"'{file_path}' does not exist on the adapter workspace")
    return resolved


def gerber_layer(name: str) -> str:
    """What a gerber or drill file is, from its KiCad name."""
    low = name.lower()
    if low.endswith((".drl", ".xln")):
        return "drill"
    for word, kind in _LAYER_WORDS:
        if word.lower() in low:
            return kind
    if re.search(r"\.g\d+$", low):
        return "copper"
    return "other"


def _walk(folder: Path, keep: Any) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fname in sorted(filenames):
            p = Path(dirpath) / fname
            if keep(p):
                found.append(p)
    return found


def select_files(kind: str, path: Path) -> tuple[Path, list[Path]]:
    """``(folder, files)`` to record for ``kind`` from a file or folder; files[0] is primary."""
    if path.is_file():
        folder = path.parent
        if kind in ("schematic", "pcb_layout"):
            ext = ".kicad_sch" if kind == "schematic" else ".kicad_pcb"
            if path.suffix != ext:
                raise ValueError(f"a {kind} is a {ext} file, not '{path.name}'")
            extra = [
                p
                for p in sorted(folder.iterdir())
                if p != path
                and p.is_file()
                and (p.suffix in (ext, ".kicad_pro") or p.name in ("sym-lib-table", "fp-lib-table"))
            ]
            return folder, [path, *extra]
        return folder, [path]

    folder = path
    if kind == "schematic":
        files = sorted(folder.glob("*.kicad_sch"))
        extra = sorted(folder.glob("*.kicad_pro")) + [
            folder / n for n in ("sym-lib-table", "fp-lib-table") if (folder / n).is_file()
        ]
        if not files:
            raise ValueError(f"no .kicad_sch in '{folder}'")
        pro = next(iter(sorted(folder.glob("*.kicad_pro"))), None)
        files.sort(key=lambda p: (pro is None or p.stem != pro.stem, p.name))
        return folder, files + extra
    if kind == "pcb_layout":
        files = sorted(folder.glob("*.kicad_pcb"))
        if not files:
            raise ValueError(f"no .kicad_pcb in '{folder}'")
        extra = sorted(folder.glob("*.kicad_pro"))
        if (folder / "fp-lib-table").is_file():
            extra.append(folder / "fp-lib-table")
        return folder, files + extra
    if kind == "gerber":
        files = [
            p for p in sorted(folder.iterdir()) if p.is_file() and _GERBER_SUFFIX.match(p.suffix)
        ]
        if not files:
            raise ValueError(f"no gerber or drill files in '{folder}'")
        files.sort(key=lambda p: (not ("F_Cu" in p.name or p.suffix.lower() == ".gtl"), p.name))
        return folder, files
    if kind == "pick_and_place":
        files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in (".csv", ".pos")]
        if not files:
            raise ValueError(f"no .csv or .pos placement file in '{folder}'")
        return folder, files
    if kind == "pinmap":
        files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in (".csv", ".json")]
        if not files:
            raise ValueError(f"no .csv or .json pin map in '{folder}'")
        return folder, files
    if kind == "firmware_source":
        files = [folder / n for n in ("platformio.ini", "CMakeLists.txt") if (folder / n).is_file()]
        for sub in _SOURCE_DIRS:
            if (folder / sub).is_dir():
                files += _walk(folder / sub, lambda p: p.suffix.lower() in _SOURCE_SUFFIXES)
        if not files:
            raise ValueError(f"no platformio.ini or src/ sources in '{folder}'")
        main = next(
            (p for p in files if p.parent.name == "src" and p.stem == "main"),
            next((p for p in files if p.suffix in (".c", ".cpp")), files[0]),
        )
        files.remove(main)
        build = folder / ".pio" / "build"
        outputs = sorted(p for p in build.glob("*/*") if p.is_file() and p.name in _BUILD_OUTPUTS)
        return folder, [main, *files, *outputs]
    raise ValueError(f"unknown kind '{kind}'")


def _content_type(name: str) -> str:
    low = name.lower()
    if low.endswith(".svg"):
        return "image/svg+xml"
    if low.endswith((".elf", ".bin", ".hex")):
        return "application/octet-stream"
    if low.endswith(".csv"):
        return "text/csv"
    return "text/plain"


def make_design_files_recorder(
    twin: Any,
    project_backend: Any = None,
    mcp_bridge: Any = None,
    workspace_dir: str | None = None,
) -> Any:
    """Return an async ``record(...)`` that persists design files as a work product."""

    root = Path(workspace_dir) if workspace_dir else None

    async def _preview(kind: str, primary: Path) -> tuple[list[tuple[str, bytes]], str | None]:
        if kind not in ("schematic", "pcb_layout"):
            return [], None
        if mcp_bridge is None:
            return [], "no MCP bridge for kicad.export_svg"
        arg = "schematic_file" if kind == "schematic" else "pcb_file"
        try:
            out = await mcp_bridge.invoke("kicad.export_svg", {arg: str(primary)})
        except Exception as exc:  # noqa: BLE001 -- the preview is best effort
            logger.warning("design_files_preview_failed", kind=kind, error=str(exc))
            return [], f"kicad.export_svg failed: {exc}"
        data = out.get("data", out) if isinstance(out, dict) else {}
        images: list[tuple[str, bytes]] = []
        for item in data.get("files") or []:
            try:
                p = resolve_in_workspace(str(item.get("file")), root)
                images.append((f"preview/{p.name}", p.read_bytes()))
            except (OSError, ValueError) as exc:
                return [], f"preview file unreadable: {exc}"
        return images, None if images else "kicad.export_svg returned no files"

    async def record(
        *,
        work_product_type: str,
        file_path: str,
        name: str,
        project_id: str | None = None,
        source_node_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        preview: bool = True,
        item_key: str | None = None,
        supersedes: str | None = None,
        change_reason: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        from digital_twin.storage.work_product_blobs import store_work_product_blob
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        kind = KINDS.get(work_product_type)
        if kind is None:
            raise ValueError(
                f"work_product_type must be one of {', '.join(KINDS)}, not '{work_product_type}'"
            )
        if not name or not isinstance(name, str):
            raise ValueError("'name' is required")
        sources = [str(s) for s in source_node_ids or []]
        for s in sources:
            try:
                UUID(s)
            except ValueError as exc:
                raise ValueError(f"source node id '{s}' is not a UUID") from exc

        path = resolve_in_workspace(file_path, root)
        folder, files = select_files(work_product_type, path)
        if len(files) > MAX_FILES:
            raise ValueError(f"{len(files)} files is over the {MAX_FILES}-file limit")
        payload = [(p.relative_to(folder).as_posix(), p.read_bytes()) for p in files]
        total = sum(len(b) for _, b in payload)
        if total > MAX_BYTES:
            raise ValueError(f"{total} bytes is over the {MAX_BYTES}-byte limit")

        plan = await plan_definition_revision(
            twin,
            item_type=work_product_type,
            name=name,
            project_id=project_id,
            default_author=kind.author,
            item_key=item_key,
            supersedes=supersedes,
            change_reason=change_reason,
            run_id=run_id,
        )
        with tracer.start_as_current_span("twin.record_design_files") as span:
            span.set_attribute("design_files.type", work_product_type)
            span.set_attribute("design_files.count", len(payload))
            span.set_attribute("design_files.bytes", total)
            wp_id = uuid4()
            primary_name, primary_bytes = payload[0]
            digest = hashlib.sha256()
            for rel, data in sorted(payload):
                digest.update(rel.encode())
                digest.update(hashlib.sha256(data).digest())
            content_hash = digest.hexdigest()

            images, preview_error = (
                await _preview(work_product_type, files[0]) if preview else ([], None)
            )

            design_files: dict[str, str] = {}
            image_files: dict[str, str] = {}
            primary_key: str | None = None
            store_error: str | None = None
            try:
                for rel, data in payload:
                    key = store_work_product_blob(
                        str(wp_id), rel, data, content_type=_content_type(rel)
                    )
                    design_files[rel] = key
                    if rel == primary_name:
                        primary_key = key
                for rel, data in images:
                    image_files[rel] = store_work_product_blob(
                        str(wp_id), rel, data, content_type="image/svg+xml"
                    )
            except Exception as exc:  # noqa: BLE001 -- keep the node, like the BOM recorder
                store_error = str(exc)
                logger.warning("design_files_blob_store_failed", error=store_error)

            meta: dict[str, Any] = {
                "original_filename": Path(primary_name).name,
                "content_sha256": content_hash,
                "authored_by": kind.author,
                "source_path": str(path),
                "files": [{"path": rel, "bytes": len(data)} for rel, data in payload],
                "file_count": len(payload),
                "total_bytes": total,
                "design_files": design_files,
                "source_work_product_ids": sources,
            }
            if primary_key:
                meta["minio_object_key"] = primary_key
            if image_files:
                meta["image_files"] = image_files
                meta["preview_files"] = sorted(image_files)
            if preview_error:
                meta["preview_error"] = preview_error
            if store_error:
                meta["store_error"] = store_error
            if work_product_type == "gerber":
                meta["layers"] = [{"path": rel, "layer": gerber_layer(rel)} for rel, _ in payload]
            if work_product_type == "firmware_source":
                meta["build_outputs"] = [
                    rel for rel, _ in payload if Path(rel).name in _BUILD_OUTPUTS
                ]
            if metadata:
                meta.update({k: v for k, v in metadata.items() if k not in meta})
            if plan is not None:
                meta.update(plan.stamp())

            now = datetime.now(UTC)
            fmt = Path(primary_name).suffix.lstrip(".").lower() or kind.fmt
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType(kind.wp_type),
                domain=kind.domain,
                file_path="" if primary_key else str(files[0]),
                content_hash=content_hash,
                format=fmt,
                metadata=meta,
                created_at=now,
                updated_at=now,
                created_by=kind.author,
                project_id=project_id,
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            linked_sources: list[str] = []
            for s in sources:
                try:
                    await twin.add_edge(UUID(node_id), UUID(s), EdgeType.GENERATED_FROM)
                    linked_sources.append(s)
                except Exception as exc:  # noqa: BLE001 -- an edge is best effort
                    logger.warning("design_files_source_edge_failed", source=s, error=str(exc))

            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, node_id, name, work_product_type
                    )
                    linked = True
                except Exception as exc:  # noqa: BLE001 -- a link is best effort
                    logger.warning("design_files_project_link_failed", error=str(exc))

            result: dict[str, Any] = {
                "node_id": node_id,
                "work_product_type": work_product_type,
                "files": len(payload),
                "bytes": total,
                "primary_file": primary_name,
                "previews": sorted(image_files),
                "generated_from": linked_sources,
                "project_linked": linked,
                "content_hash": content_hash,
            }
            if preview_error:
                result["preview_error"] = preview_error
            if store_error:
                result["store_error"] = store_error
            await finish_definition_revision(
                twin, plan, getattr(created, "id", wp_id), name=name, result=result
            )
            logger.info(
                "design_files_recorded",
                node_id=node_id,
                type=work_product_type,
                files=len(payload),
                bytes=total,
                previews=len(image_files),
                project_id=project_id,
                item_ref=result.get("item_ref"),
            )
            return result

    return record
