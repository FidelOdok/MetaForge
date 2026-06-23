"""Gateway-side client for the FreeCAD adapter's parametric tools (MET-531).

When a user drags a rigid group and clicks *Apply*, the constraint route turns
the delta into a named parameter and **binds it into the live FreeCAD model** so
the change is parametric and re-solvable — not just a suggestion string. This
module is the seam that reaches the containerized freecad adapter over MCP,
mirroring how ``import_service`` reaches the OCCT/KiCad adapters over HTTP
(``METAFORGE_ADAPTER_*_URL`` → JSON-RPC ``tool/call`` at ``/mcp``).

The binding is two MCP calls against the caller's live session:

1. ``create_variable_set`` — ensure an ``App::VarSet`` holds the named parameter
   (e.g. ``motor_group_position_x = 5.0 mm``);
2. ``set_expression`` — bind the dragged object's placement component to that
   variable (``Placement.Base.x = <<ConstraintParams>>.motor_group_position_x``),

after which FreeCAD recomputes — the model is now driven by the parameter.

Best-effort: a missing/unreachable adapter never breaks Apply; the route still
returns its suggestion with ``bound=False`` and a reason.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.constraint.freecad_client")

_FREECAD_URL = os.getenv("METAFORGE_ADAPTER_FREECAD_URL", "http://localhost:8102")

# Default VarSet that holds Apply-authored parameters in a session.
DEFAULT_VARSET = "ConstraintParams"


class FreecadBindingError(RuntimeError):
    """Raised when a parametric binding MCP call fails (adapter reachable but errored)."""


class FreecadMcpClient:
    """Thin async JSON-RPC client for the freecad adapter's ``/mcp`` endpoint."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or _FREECAD_URL).rstrip("/")
        self.timeout = timeout

    async def call_tool(self, tool_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one adapter tool; return its ``result.data``. Raises on RPC error."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tool/call",
            "params": {"tool_id": tool_id, "arguments": arguments},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/mcp", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise FreecadBindingError(f"{tool_id}: {body['error'].get('message', body['error'])}")
        result = body.get("result", {})
        # Adapter envelope is {tool_id, status, data, duration_ms}; tolerate either.
        return result.get("data", result) if isinstance(result, dict) else {}

    async def apply_parametric_binding(
        self,
        *,
        session_id: str,
        obj_id: str,
        parameter: str,
        value: float,
        property_path: str,
        varset_name: str = DEFAULT_VARSET,
        var_type: str = "length",
    ) -> dict[str, Any]:
        """Create/refresh the parameter VarSet and bind ``obj.property_path`` to it.

        Returns ``{bound, parameter, value, expression, varset}``. The expression
        references the VarSet by label (``<<name>>.parameter``) so it survives
        re-solves. Recompute happens inside each tool call.
        """
        with tracer.start_as_current_span("constraint.apply_parametric_binding") as span:
            span.set_attribute("freecad.session_id", session_id)
            span.set_attribute("freecad.obj_id", obj_id)
            span.set_attribute("constraint.parameter", parameter)

            await self.call_tool(
                "freecad.create_variable_set",
                {
                    "session_id": session_id,
                    "name": varset_name,
                    "variables": {parameter: {"value": value, "type": var_type}},
                },
            )
            expression = f"<<{varset_name}>>.{parameter}"
            await self.call_tool(
                "freecad.set_expression",
                {
                    "session_id": session_id,
                    "obj_id": obj_id,
                    "property": property_path,
                    "expression": expression,
                },
            )
            logger.info(
                "constraint_parametric_bound",
                session_id=session_id,
                obj_id=obj_id,
                parameter=parameter,
                property=property_path,
            )
            return {
                "bound": True,
                "parameter": parameter,
                "value": value,
                "expression": expression,
                "varset": varset_name,
            }


# Module-level default; patchable in tests (mirrors api_gateway.twin.routes._twin).
default_freecad_client = FreecadMcpClient()
