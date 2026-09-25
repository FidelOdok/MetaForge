"""Shared ``twin.commit_geometry`` helper for CAD-generating skills.

Any skill that produces a STEP file via a CAD adapter tool (cadquery/freecad
``create_parametric``, ``cadquery.generate_enclosure``, ...) and wants to
persist it into the Twin needs the exact same three steps: resolve the
adapter-returned file path against the shared workspace, read + base64 it,
and call ``twin.commit_geometry``. Centralized here so the FORGE-79
relative-path fix (and any future fix to this path) applies to every caller,
not just the one skill that happened to get patched first (FORGE-84).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import structlog

from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)

# FORGE-100: canonical measured-property keys a constraint expression can
# read directly off a committed cad_model's metadata (e.g.
# `float(wp.metadata.get('mass_kg', 0)) <= 4.5`). Previously nothing wrote
# these at all -- a CAD tool computed volume/mass/bounding-box and returned
# them in its own response, but commit_geometry() never threaded them
# through, so every constraint referencing a measured property silently
# read the default the expression's own `.get(key, 0)` supplied: an ==/>=
# comparison always FAILED (nothing ever equals/exceeds 0), and a <=/<
# comparison always vacuously PASSED, regardless of the design's real
# dimensions. Neither is a real engineering verdict.
_CANONICAL_MEASURED_KEYS = ("volume_mm3", "surface_area_mm2", "mass_kg")


def measured_metadata_from_cad_result(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the canonical measured-property keys a CAD tool's own result
    already computed, shaped for ``commit_geometry()``'s ``extra_metadata``.

    Deliberately narrow: only copies keys the tool result actually contains
    (``create_parametric``/``generate_enclosure``/``create_assembly`` all
    return ``volume_mm3``/``surface_area_mm2``/``bounding_box``; ``mass_kg``
    only when the tool was given a ``material`` to look up a density for) --
    never fabricates a value the tool didn't itself compute.
    """
    metadata: dict[str, Any] = {
        key: result[key] for key in _CANONICAL_MEASURED_KEYS if key in result
    }
    bbox = result.get("bounding_box")
    if isinstance(bbox, dict):
        metadata["bbox_mm"] = bbox
    return metadata


async def commit_geometry(
    mcp: McpBridge,
    *,
    cad_file: str,
    name: str,
    project_id: str | None,
    domain: str = "mechanical",
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[bool, str | None, str | None, str | None]:
    """Best-effort persist an exported STEP file via ``twin.commit_geometry``.

    CAD generation tools write the STEP file inside their own adapter
    container and echo back the same path they were given (e.g.
    ``output/plate_None.step``) rather than an absolute one. That path is
    only valid relative to the adapter's own CWD, but it lands on the
    ``adapter-workspace`` volume the adapter and this gateway process both
    mount (the adapter at its CWD, this process at ``ADAPTER_WORKSPACE_DIR``,
    default ``/workspace``) -- so a relative *cad_file* is resolved against
    that shared root, mirroring
    ``api_gateway.twin.regenerate_geometry._regenerate_via_cadquery``
    (FORGE-79).

    ``extra_metadata`` (FORGE-100) lands as top-level keys on the committed
    work product's metadata -- pass ``measured_metadata_from_cad_result()``'s
    output here so a constraint expression can read real measured values
    instead of a default.

    Returns:
        (committed, twin_node_id, model_url, commit_error).
    """
    if not await mcp.is_available("twin.commit_geometry"):
        return False, None, None, "twin.commit_geometry tool is not available"

    resolved_path = Path(cad_file)
    if not resolved_path.is_absolute():
        workspace_root = Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        resolved_path = workspace_root / cad_file

    try:
        step_base64 = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning(
            "Could not read generated CAD file to commit it",
            cad_file=cad_file,
            resolved_path=str(resolved_path),
            error=str(exc),
        )
        return False, None, None, f"could not read {resolved_path}: {exc}"

    arguments: dict[str, Any] = {
        "name": name,
        "step_base64": step_base64,
        "domain": domain,
        "format": "step",
    }
    if project_id:
        arguments["project_id"] = project_id
    if extra_metadata:
        arguments["extra_metadata"] = extra_metadata

    try:
        result = await mcp.invoke("twin.commit_geometry", arguments, timeout=60)
    except Exception as exc:
        logger.warning("twin.commit_geometry failed", error=str(exc))
        return False, None, None, str(exc)

    return True, result.get("node_id"), result.get("model_url"), None
