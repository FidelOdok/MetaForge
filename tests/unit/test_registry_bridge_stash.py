"""RegistryMcpBridge commit-by-reference stash — the gateway chat seam (MET-10)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from skill_registry.registry_bridge import RegistryMcpBridge


class _Client:
    """Fake McpClient: echoes a canned result per tool, records commit args."""

    def __init__(self) -> None:
        self.commit_args: dict[str, Any] | None = None

    async def call_tool(self, request: Any) -> Any:
        if request.tool_id == "freecad.export_model":
            return SimpleNamespace(
                status="success", data={"obj_id": "assembly_3", "step_base64": "STEP-BLOB"}
            )
        if request.tool_id == "twin.commit_geometry":
            self.commit_args = dict(request.arguments)
            if not request.arguments.get("step_base64"):
                return SimpleNamespace(status="error", data={})
            return SimpleNamespace(status="success", data={"node_id": "n1"})
        return SimpleNamespace(status="success", data={})


class _Registry:
    def __init__(self, client: _Client) -> None:
        self._client = client

    def get_adapter_for_tool(self, tool_id: str) -> str:
        return tool_id.split(".", 1)[0]

    def get_client(self, adapter_id: str) -> _Client:
        return self._client

    def get_tool(self, tool_id: str) -> object:
        return object()


@pytest.mark.asyncio
async def test_bridge_fills_commit_from_prior_export() -> None:
    client = _Client()
    bridge = RegistryMcpBridge(_Registry(client))  # type: ignore[arg-type]

    # export remembers the STEP by (session_id, obj_id)
    await bridge.invoke("freecad.export_model", {"session_id": "s1", "obj_id": "assembly_3"})
    # commit by reference — no step_base64 in the args
    result = await bridge.invoke(
        "twin.commit_geometry",
        {"session_id": "s1", "obj_id": "assembly_3", "name": "Gimbal Yaw"},
    )

    assert client.commit_args is not None
    assert client.commit_args["step_base64"] == "STEP-BLOB"  # filled by the bridge
    assert result["node_id"] == "n1"
