"""Gateway→freecad MCP client for parametric Apply binding (MET-531)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_gateway.constraint.freecad_client import FreecadBindingError, FreecadMcpClient


def _resp(json_body: dict) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=json_body)
    return r


def _patched_client(post_return: MagicMock) -> tuple[FreecadMcpClient, AsyncMock]:
    mock_client = AsyncMock()
    mock_client.post.return_value = post_return
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return FreecadMcpClient(base_url="http://freecad:8102"), mock_client


class TestCallTool:
    async def test_unwraps_result_data(self) -> None:
        body = {"result": {"status": "success", "data": {"obj_id": "x"}}}
        client, mc = _patched_client(_resp(body))
        with patch("httpx.AsyncClient", return_value=mc):
            out = await client.call_tool("freecad.create_variable_set", {"session_id": "s"})
        assert out == {"obj_id": "x"}
        # Posts JSON-RPC tool/call to the adapter /mcp endpoint.
        sent = mc.post.call_args
        assert sent.args[0] == "http://freecad:8102/mcp"
        assert sent.kwargs["json"]["method"] == "tool/call"
        assert sent.kwargs["json"]["params"]["tool_id"] == "freecad.create_variable_set"

    async def test_rpc_error_raises(self) -> None:
        client, mc = _patched_client(_resp({"error": {"code": -32000, "message": "boom"}}))
        with patch("httpx.AsyncClient", return_value=mc):
            with pytest.raises(FreecadBindingError, match="boom"):
                await client.call_tool("freecad.set_expression", {})


class TestApplyParametricBinding:
    async def test_creates_varset_then_sets_expression(self) -> None:
        client = FreecadMcpClient(base_url="http://freecad:8102")
        calls: list[tuple[str, dict]] = []

        async def fake_call(tool_id: str, arguments: dict) -> dict:
            calls.append((tool_id, arguments))
            return {}

        with patch.object(client, "call_tool", side_effect=fake_call):
            out = await client.apply_parametric_binding(
                session_id="s1",
                obj_id="primitive_1",
                parameter="motor_position_x",
                value=5.0,
                property_path="Placement.Base.x",
            )

        assert [c[0] for c in calls] == [
            "freecad.create_variable_set",
            "freecad.set_expression",
        ]
        # VarSet carries the named parameter as a length.
        vs_args = calls[0][1]
        assert vs_args["session_id"] == "s1"
        assert vs_args["variables"]["motor_position_x"]["value"] == 5.0
        assert vs_args["variables"]["motor_position_x"]["type"] == "length"
        # Expression binds the placement component to the VarSet variable by label.
        se_args = calls[1][1]
        assert se_args["obj_id"] == "primitive_1"
        assert se_args["property"] == "Placement.Base.x"
        assert se_args["expression"] == "<<ConstraintParams>>.motor_position_x"

        assert out["bound"] is True
        assert out["expression"] == "<<ConstraintParams>>.motor_position_x"
