"""Deterministic multi-part assembly authoring (MET-10).

Drives the FreeCAD MCP tools to author a real multi-part assembly and commit it
to the twin — with **no LLM**. The design flow only produces a single primitive,
and the chat agent can author an assembly but can't reliably commit it (it can't
thread the STEP blob). This builder does it deterministically from a declarative
spec: each part is a primitive, the parts are joined into an assembly, exported,
and committed through the geometry recorder (the reliable, blob-in-Python path).
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    """Unwrap an MCP result envelope, raising on error."""
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RuntimeError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


async def build_assembly(
    *,
    bridge: Any,
    recorder: Any,
    name: str,
    parts: list[dict[str, Any]],
    project_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Author + commit a multi-part assembly. Returns the recorder result."""

    async def invoke(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return _data(await bridge.invoke(tool, args), tool)

    session = await invoke("freecad.open_session", {"name": name})
    sid = session.get("session_id")
    if not sid:
        raise RuntimeError("freecad.open_session returned no session_id")

    part_ids: list[str] = []
    for part in parts:
        prim = await invoke(
            "freecad.create_primitive",
            {
                "session_id": sid,
                "kind": part["kind"],
                "name": part["name"],
                "parameters": part.get("parameters") or {},
            },
        )
        obj_id = prim.get("obj_id")
        if not obj_id:
            raise RuntimeError(f"create_primitive returned no obj_id for {part['name']!r}")
        position = part.get("position")
        if position:
            await invoke(
                "freecad.transform_object",
                {"session_id": sid, "obj_id": obj_id, "position": position},
            )
        part_ids.append(obj_id)

    assembly = await invoke("freecad.create_assembly", {"session_id": sid, "name": name})
    asm_id = assembly.get("obj_id")
    if not asm_id:
        raise RuntimeError("freecad.create_assembly returned no obj_id")
    for part_id in part_ids:
        await invoke(
            "freecad.add_part_to_assembly",
            {"session_id": sid, "assembly_id": asm_id, "part_id": part_id},
        )

    export = await invoke("freecad.export_model", {"session_id": sid, "obj_id": asm_id})
    step_b64 = (
        export.get("step_base64")
        or export.get("base64")
        or export.get("step")
        or export.get("content")
    )
    if not step_b64:
        raise RuntimeError(f"freecad.export_model returned no STEP (keys: {list(export)})")

    rec = await recorder(
        step_base64=step_b64,
        name=name,
        project_id=project_id,
        session_id=session_id,
        extra_metadata={"assembly": True, "part_count": len(parts)},
    )
    logger.info(
        "cad_assembly_built",
        name=name,
        parts=len(parts),
        node_id=(rec.get("node_id") if isinstance(rec, dict) else None),
    )
    return rec if isinstance(rec, dict) else {}
