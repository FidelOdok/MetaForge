"""Regenerate CAD geometry from an edited script (MET-630, Phase 3b apply action).

Closes the loop the dashboard's parameter panel opens: a human edits a
parameter, that submits a ``twin.propose_change`` proposal with
``action: "regenerate_geometry"``, and on approval this module actually
re-executes the (parameter-substituted) generation script and commits the
result — the ``regenerate_geometry``/``update_properties`` apply actions
were previously an explicit no-op (see ``api_gateway/assistant/apply.py``).

Mirrors ``api_gateway.twin.boolean_ops.perform_boolean_op``: drives the
containerized CadQuery adapter via the shared ``McpBridge``, reads the
result STEP from the ``ADAPTER_WORKSPACE_DIR`` volume the gateway and
adapter containers share, then commits via the geometry recorder so the
new node gets git-versioned script history + a SUPERSEDES link to the
part it replaces (both already wired in ``geometry_recorder.py``).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.regenerate_geometry")

_PROPERTY_KEYS = ("volume_mm3", "surface_area_mm2", "bounding_box")


class RegenerateGeometryError(Exception):
    """The regeneration script failed or produced no usable output."""


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    """Unwrap an MCP result envelope, raising on error (mirrors boolean_ops.py)."""
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RegenerateGeometryError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


async def perform_regenerate_geometry(
    *,
    bridge: Any,
    recorder: Any,
    script_source: str,
    name: str,
    project_id: str | None = None,
    domain: str = "mechanical",
    parameters: dict[str, Any] | None = None,
    source_tool: str = "twin.propose_change:regenerate_geometry",
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute ``script_source`` via CadQuery and commit the resulting STEP.

    Returns the geometry recorder's result dict (``node_id``, ``model_url``,
    ``git_commit_sha``, ...). Raises ``RegenerateGeometryError`` if the
    script produces no output — any other exception (adapter unreachable,
    script sandbox rejection) propagates for the caller to map/report.
    """
    with tracer.start_as_current_span("twin.regenerate_geometry") as span:
        span.set_attribute("regenerate_geometry.name", name)

        result = _data(
            await bridge.invoke("cadquery.execute_script", {"script": script_source}),
            "cadquery.execute_script",
        )
        cad_file = result.get("cad_file")
        if not cad_file:
            raise RegenerateGeometryError("cadquery.execute_script produced no output file")

        root = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        result_path = Path(cad_file)
        if not result_path.is_absolute():
            result_path = root / cad_file
        if not result_path.exists():
            raise RegenerateGeometryError(f"Regenerated file not found at {result_path}")

        step_b64 = base64.b64encode(result_path.read_bytes()).decode("ascii")
        properties = {k: result[k] for k in _PROPERTY_KEYS if k in result}

        return await recorder(  # type: ignore[no-any-return]
            step_base64=step_b64,
            name=name,
            project_id=project_id,
            domain=domain,
            fmt="step",
            source_tool=source_tool,
            script_source=script_source,
            parameters=parameters,
            properties=properties or None,
        )
