"""The Blender adapter reads a model larger than aiohttp's 1 MiB default.

``twin.render_work_product`` sends the model inline; a converted airframe
was refused with "Maximum request body size 1048576 exceeded".
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.asyncio


class _EchoServer:
    adapter_id = "blender"
    version = "test"
    tool_ids = ["blender.render"]

    def __init__(self) -> None:
        self.bodies: list[int] = []

    async def handle_request(self, body: str) -> str:
        self.bodies.append(len(body))
        return json.dumps({"jsonrpc": "2.0", "id": "1", "result": {"data": {"ok": True}}})


def test_the_default_limit_is_far_above_one_mebibyte() -> None:
    from tool_registry.tools.blender.entrypoint import max_request_bytes

    assert max_request_bytes({}) == 256 * 1024 * 1024
    assert max_request_bytes({"BLENDER_MAX_REQUEST_MB": "64"}) == 64 * 1024 * 1024
    assert max_request_bytes({"BLENDER_MAX_REQUEST_MB": "nonsense"}) == 256 * 1024 * 1024
    assert max_request_bytes({"BLENDER_MAX_REQUEST_MB": "0"}) == 1024 * 1024


async def test_a_model_over_one_mebibyte_reaches_the_server() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tool_registry.tools.blender.entrypoint import build_app

    server = _EchoServer()
    async with TestClient(TestServer(build_app(server))) as client:
        resp = await client.post("/mcp", data="x" * (3 * 1024 * 1024))
        assert resp.status == 200
    assert server.bodies == [3 * 1024 * 1024]


async def test_an_oversized_request_is_a_json_rpc_error() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tool_registry.tools.blender.entrypoint import build_app

    server = _EchoServer()
    async with TestClient(TestServer(build_app(server, max_bytes=1024))) as client:
        resp = await client.post("/mcp", data="x" * 4096)
        assert resp.status == 413
        payload = json.loads(await resp.text())
    assert "reads at most 1024 bytes" in payload["error"]["data"]["details"]
    assert "BLENDER_MAX_REQUEST_MB" in payload["error"]["data"]["details"]
    assert server.bodies == []
