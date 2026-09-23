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


async def commit_geometry(
    mcp: McpBridge,
    *,
    cad_file: str,
    name: str,
    project_id: str | None,
    domain: str = "mechanical",
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

    try:
        result = await mcp.invoke("twin.commit_geometry", arguments, timeout=60)
    except Exception as exc:
        logger.warning("twin.commit_geometry failed", error=str(exc))
        return False, None, None, str(exc)

    return True, result.get("node_id"), result.get("model_url"), None
