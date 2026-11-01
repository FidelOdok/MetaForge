"""Unit tests for the Meshy cloud mesh adapter.

No network: the client is driven through an injected ``httpx.AsyncClient``
backed by ``httpx.MockTransport``, and downloads land in ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_core.annotations import annotations_for
from tool_registry.tools.meshy.client import MeshyClient, MeshyError, validate_formats
from tool_registry.tools.meshy.mcp_adapter import MeshyMcpServer

ASSET = "https://assets.meshy.ai/tasks/abc/model.stl?Expires=1"


class FakeMeshy:
    """A scripted Meshy: records requests, replies from a status sequence."""

    def __init__(self, statuses: list[str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses = statuses or ["SUCCEEDED"]
        self.create_status = 202
        self.create_body: dict[str, Any] = {"result": "task-123"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "assets.meshy.ai":
            return httpx.Response(200, content=b"solid mesh\nendsolid mesh\n")
        if request.method == "POST":
            return httpx.Response(self.create_status, json=self.create_body)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        body: dict[str, Any] = {
            "id": request.url.path.rsplit("/", 1)[-1],
            "status": status,
            "progress": 100 if status == "SUCCEEDED" else 40,
            "model_urls": {"stl": ASSET, "glb": ASSET.replace("stl", "glb")}
            if status == "SUCCEEDED"
            else {},
            "consumed_credits": 5,
        }
        if status == "FAILED":
            body["task_error"] = {"message": "prompt was moderated"}
        return httpx.Response(200, json=body)


def make_server(fake: FakeMeshy, workspace: Path, key: str = "test-key") -> MeshyMcpServer:
    client = MeshyClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        api_key=key,
    )
    server = MeshyMcpServer(client=client, workspace_dir=workspace)
    server.poll_interval_seconds = 0
    return server


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


async def test_text_to_3d_sends_a_preview_with_bearer_key(tmp_path: Path) -> None:
    fake = FakeMeshy()
    server = make_server(fake, tmp_path)
    result = await server.handle_text_to_3d({"prompt": "a drone propeller guard"})

    assert result == {"task_id": "task-123", "kind": "text_to_3d", "status": "PENDING"}
    request = fake.requests[0]
    assert request.url.path == "/openapi/v2/text-to-3d"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["mode"] == "preview"
    assert body["target_formats"] == ["glb", "stl"]


async def test_image_to_3d_uses_the_v1_endpoint(tmp_path: Path) -> None:
    fake = FakeMeshy()
    server = make_server(fake, tmp_path)
    result = await server.handle_image_to_3d(
        {"image_url": "https://example.com/part.png", "target_polycount": 5000}
    )

    assert result["kind"] == "image_to_3d"
    assert fake.requests[0].url.path == "/openapi/v1/image-to-3d"
    body = json.loads(fake.requests[0].content)
    assert body["target_polycount"] == 5000
    assert body["should_remesh"] is True


async def test_image_to_3d_refuses_a_local_path(tmp_path: Path) -> None:
    fake = FakeMeshy()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="public https"):
        await server.handle_image_to_3d({"image_url": "/home/me/part.png"})
    assert fake.requests == []


async def test_refine_targets_the_preview(tmp_path: Path) -> None:
    fake = FakeMeshy()
    server = make_server(fake, tmp_path)
    await server.handle_refine_texture({"preview_task_id": "prev-1", "enable_pbr": True})

    body = json.loads(fake.requests[0].content)
    assert body == {
        "mode": "refine",
        "preview_task_id": "prev-1",
        "target_formats": ["glb", "stl"],
        "enable_pbr": True,
    }


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "refused the API key"), (402, "no credits"), (429, "rate-limited")],
)
async def test_http_errors_raise_with_a_reason(tmp_path: Path, status: int, reason: str) -> None:
    fake = FakeMeshy()
    fake.create_status = status
    fake.create_body = {"message": "nope"}
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match=reason):
        await server.handle_text_to_3d({"prompt": "bracket"})


async def test_missing_key_raises_without_calling_meshy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MESHY_API_KEY", raising=False)
    fake = FakeMeshy()
    client = MeshyClient(client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    with pytest.raises(MeshyError, match="MESHY_API_KEY"):
        await client.get_task("text_to_3d", "task-1")
    assert fake.requests == []


async def test_overlong_prompt_is_refused_before_the_call(tmp_path: Path) -> None:
    fake = FakeMeshy()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="800"):
        await server.handle_text_to_3d({"prompt": "x" * 801})
    assert fake.requests == []


def test_formats_are_validated() -> None:
    assert validate_formats(None) == ["glb", "stl"]
    assert validate_formats([".STL", "3mf"]) == ["stl", "3mf"]
    with pytest.raises(MeshyError, match="unsupported"):
        validate_formats(["step"])


async def test_bad_formats_surface_as_value_error(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(), tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        await server.handle_text_to_3d({"prompt": "cup", "formats": ["step"]})


def test_base_url_override(monkeypatch) -> None:
    monkeypatch.setenv("MESHY_BASE_URL", "https://proxy.example/")
    client = MeshyClient(api_key="k")
    assert client._base_url == "https://proxy.example"


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


async def test_get_task_waits_until_done(tmp_path: Path) -> None:
    fake = FakeMeshy(statuses=["PENDING", "IN_PROGRESS", "SUCCEEDED"])
    server = make_server(fake, tmp_path)
    result = await server.handle_get_task(
        {"task_id": "task-123", "kind": "text_to_3d", "wait_seconds": 30}
    )

    assert result["status"] == "SUCCEEDED"
    assert result["formats"] == ["glb", "stl"]
    assert len(fake.requests) == 3


async def test_get_task_without_wait_returns_the_running_status(tmp_path: Path) -> None:
    fake = FakeMeshy(statuses=["IN_PROGRESS"])
    server = make_server(fake, tmp_path)
    result = await server.handle_get_task(
        {"task_id": "task-123", "kind": "text_to_3d", "wait_seconds": 0}
    )

    assert result["status"] == "IN_PROGRESS"
    assert result["progress"] == 40
    assert len(fake.requests) == 1


async def test_get_task_reports_meshys_failure_reason(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(statuses=["FAILED"]), tmp_path)
    result = await server.handle_get_task({"task_id": "t-1", "kind": "image_to_3d"})
    assert result["status"] == "FAILED"
    assert result["error"] == "prompt was moderated"


async def test_get_task_rejects_an_unknown_kind(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(), tmp_path)
    with pytest.raises(ValueError, match="'kind'"):
        await server.handle_get_task({"task_id": "t-1", "kind": "text_to_cad"})


async def test_task_id_cannot_escape_the_workspace(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(), tmp_path)
    with pytest.raises(ValueError, match="not a Meshy task id"):
        await server.handle_download({"task_id": "../../etc/passwd", "kind": "text_to_3d"})


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


async def test_download_writes_the_mesh_into_the_workspace(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(), tmp_path)
    result = await server.handle_download(
        {"task_id": "task-123", "kind": "text_to_3d", "formats": ["stl", "3mf"]}
    )

    assert result["files"] == [
        {"format": "stl", "file_path": "output/meshy/task-123.stl", "size_bytes": 25}
    ]
    assert result["missing_formats"] == ["3mf"]
    written = tmp_path / "output" / "meshy" / "task-123.stl"
    assert written.read_bytes().startswith(b"solid mesh")
    assert not list(written.parent.glob("*.part"))


async def test_download_refuses_an_unfinished_task(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(statuses=["IN_PROGRESS"]), tmp_path)
    with pytest.raises(ValueError, match="not SUCCEEDED"):
        await server.handle_download({"task_id": "task-123", "kind": "text_to_3d"})


async def test_download_with_no_matching_format_is_an_error(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(), tmp_path)
    with pytest.raises(ValueError, match="none of"):
        await server.handle_download(
            {"task_id": "task-123", "kind": "text_to_3d", "formats": ["usdz"]}
        )


async def test_expired_download_url_leaves_no_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = MeshyClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    dest = tmp_path / "out.stl"
    with pytest.raises(MeshyError, match="expired"):
        await client.download(ASSET, dest)
    assert not dest.exists()
    assert not dest.with_suffix(".stl.part").exists()


async def test_download_refuses_plain_http(tmp_path: Path) -> None:
    client = MeshyClient(api_key="k")
    with pytest.raises(MeshyError, match="non-https"):
        await client.download("http://assets.meshy.ai/x.stl", tmp_path / "x.stl")


# ---------------------------------------------------------------------------
# Registration + annotations
# ---------------------------------------------------------------------------


def test_registers_five_tools(tmp_path: Path) -> None:
    server = make_server(FakeMeshy(), tmp_path)
    assert sorted(server.tool_ids) == [
        "meshy.download",
        "meshy.get_task",
        "meshy.image_to_3d",
        "meshy.refine_texture",
        "meshy.text_to_3d",
    ]


def test_spending_tools_are_writes_and_status_is_read_only() -> None:
    for tool_id in ("meshy.text_to_3d", "meshy.image_to_3d", "meshy.refine_texture"):
        hints = annotations_for(tool_id)
        assert hints["readOnlyHint"] is False
        assert hints["destructiveHint"] is False
        assert hints["openWorldHint"] is True
    status = annotations_for("meshy.get_task")
    assert status["readOnlyHint"] is True
    assert status["openWorldHint"] is True


async def test_bootstrap_registers_meshy_only_with_a_key(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.delenv("MESHY_API_KEY", raising=False)
    registry = await bootstrap_tool_registry(adapter_ids=["meshy"])
    assert registry.list_adapters() == []

    monkeypatch.setenv("MESHY_API_KEY", "test-key")
    registry = await bootstrap_tool_registry(adapter_ids=["meshy"])
    assert [a.adapter_id for a in registry.list_adapters()] == ["meshy"]


async def test_bootstrap_honours_the_per_adapter_toggle(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.setenv("MESHY_API_KEY", "test-key")
    monkeypatch.setenv("METAFORGE_ADAPTER_MESHY_ENABLED", "false")
    registry = await bootstrap_tool_registry(adapter_ids=["meshy"])
    assert registry.list_adapters() == []
