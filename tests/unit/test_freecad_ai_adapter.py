"""FreeCAD AI MCP adapter scaffold (MET-526)."""

from __future__ import annotations

import json
from typing import Any

from tool_registry.tools.freecad_ai.adapter import FreecadAiServer


def _call(tool_id: str, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tool/call",
            "params": {"tool_id": tool_id, "arguments": arguments},
        }
    )


class TestFreecadAiAdapter:
    def test_registers_curated_surface(self) -> None:
        ids = FreecadAiServer().tool_ids
        assert "freecad_ai.create_primitive" in ids
        assert "freecad_ai.add_assembly_joint" in ids  # the kinematic-solver hook
        assert "freecad_ai.set_expression" in ids  # parametric recommendations
        assert "freecad_ai.export_model" in ids
        assert all(t.startswith("freecad_ai.") for t in ids)
        assert len(ids) == 17

    async def test_forwards_short_name_and_args_to_transport(self) -> None:
        seen: dict[str, Any] = {}

        async def fake(name: str, args: dict[str, Any]) -> dict[str, Any]:
            seen["name"] = name
            seen["args"] = args
            return {"ok": True, "echo": args}

        server = FreecadAiServer(transport=fake)
        resp = json.loads(
            await server.handle_request(
                _call("freecad_ai.create_primitive", {"shape": "box", "x": 10})
            )
        )
        assert resp["result"]["status"] == "success"
        assert resp["result"]["data"] == {"ok": True, "echo": {"shape": "box", "x": 10}}
        # The FreeCAD AI server receives the unprefixed tool name.
        assert seen["name"] == "create_primitive"
        assert seen["args"] == {"shape": "box", "x": 10}

    async def test_unconfigured_transport_errors_cleanly(self) -> None:
        resp = json.loads(await FreecadAiServer().handle_request(_call("freecad_ai.measure", {})))
        assert resp["error"]["code"] == -32001  # adapter-down shape
        assert "not configured" in resp["error"]["data"]["details"].lower()
