"""Regenerate CAD geometry from an edited script (MET-630, Phase 3b apply action).

Closes the loop the dashboard's parameter panel opens: a human edits a
parameter, that submits a ``twin.propose_change`` proposal with
``action: "regenerate_geometry"``, and on approval this module actually
re-executes the (parameter-substituted) generation script and commits the
result — the ``regenerate_geometry``/``update_properties`` apply actions
were previously an explicit no-op (see ``api_gateway/assistant/apply.py``).

Supports both CAD scripting tools this codebase generates from, since
they work fundamentally differently:

- **CadQuery** (``cad_tool="cadquery"``, the default): one self-contained
  call — ``cadquery.execute_script`` writes a STEP file to the shared
  ``ADAPTER_WORKSPACE_DIR`` volume the gateway and adapter containers
  share (mirrors ``api_gateway.twin.boolean_ops``).
- **FreeCAD** (``cad_tool="freecad"``, the more commonly used tool in
  this codebase's own test suite / default authoring path — see
  ``geometry_recorder.py``'s ``source_tool="freecad.export_model"``
  default): requires a stateful session lifecycle —
  ``open_session`` -> ``execute_code`` (runs against that session's live
  document, returns an ``obj_id`` reference, not a file) ->
  ``export_model`` (session_id + obj_id -> ``step_base64`` returned
  directly in the response, no shared-volume file read needed) ->
  ``close_session``.
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


async def _regenerate_via_cadquery(
    bridge: Any, script_source: str, workspace_dir: Path | None
) -> tuple[bytes, dict[str, Any]]:
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

    properties = {k: result[k] for k in _PROPERTY_KEYS if k in result}
    return result_path.read_bytes(), properties


async def _regenerate_via_freecad(bridge: Any, script_source: str) -> tuple[bytes, dict[str, Any]]:
    session = _data(await bridge.invoke("freecad.open_session", {}), "freecad.open_session")
    session_id = session.get("session_id")
    if not session_id:
        raise RegenerateGeometryError("freecad.open_session did not return a session_id")

    try:
        exec_result = _data(
            await bridge.invoke(
                "freecad.execute_code", {"session_id": session_id, "code": script_source}
            ),
            "freecad.execute_code",
        )
        obj_id = exec_result.get("obj_id")
        if not obj_id:
            raise RegenerateGeometryError(
                "freecad.execute_code produced no result object — the script must "
                "assign its output to a variable named 'result'"
            )

        export_result = _data(
            await bridge.invoke(
                "freecad.export_model", {"session_id": session_id, "obj_id": obj_id}
            ),
            "freecad.export_model",
        )
        step_b64 = export_result.get("step_base64")
        if not step_b64:
            raise RegenerateGeometryError("freecad.export_model returned no step_base64")

        properties = {k: export_result[k] for k in _PROPERTY_KEYS if k in export_result}
        return base64.b64decode(step_b64), properties
    finally:
        # Best-effort — a stuck session shouldn't mask a successful export,
        # but leaking sessions is a real resource cost, so always try.
        try:
            await bridge.invoke("freecad.close_session", {"session_id": session_id})
        except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
            logger.warning("regenerate_geometry_session_close_failed", error=str(exc))


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
    cad_tool: str = "cadquery",
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute ``script_source`` via CadQuery or FreeCAD and commit the resulting STEP.

    Args:
        cad_tool: Which scripting dialect ``script_source`` is written in —
            ``"cadquery"`` (default) or ``"freecad"``. The two have no
            common script format, so the caller (whoever last edited the
            script) must state which one it is rather than this being
            auto-detected.

    Returns the geometry recorder's result dict (``node_id``, ``model_url``,
    ``git_commit_sha``, ...). Raises ``RegenerateGeometryError`` if the
    script produces no output, or ``ValueError`` for an unknown
    ``cad_tool`` — any other exception (adapter unreachable, script sandbox
    rejection) propagates for the caller to map/report.
    """
    with tracer.start_as_current_span("twin.regenerate_geometry") as span:
        span.set_attribute("regenerate_geometry.name", name)
        span.set_attribute("regenerate_geometry.cad_tool", cad_tool)

        if cad_tool == "cadquery":
            content, properties = await _regenerate_via_cadquery(
                bridge, script_source, workspace_dir
            )
        elif cad_tool == "freecad":
            content, properties = await _regenerate_via_freecad(bridge, script_source)
        else:
            raise ValueError(f"Unknown cad_tool '{cad_tool}' — expected 'cadquery' or 'freecad'")

        step_b64 = base64.b64encode(content).decode("ascii")

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
