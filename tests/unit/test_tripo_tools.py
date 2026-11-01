"""Unit tests for the Tripo cloud mesh adapter.

No network: the client is driven through an injected ``httpx.AsyncClient``
backed by ``httpx.MockTransport``. Response bodies follow Tripo's published
v3 API (``{"code": 0, "data": ...}`` envelopes, ``GET /tasks/{task_id}``,
``POST /files``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_core.annotations import annotations_for
from tool_registry.tools.tripo.client import TripoClient, TripoError, validate_convert_format
from tool_registry.tools.tripo.mcp_adapter import TripoMcpServer

TASK_ID = "task_abc123"
GLB_BYTES = b"glTF\x02\x00\x00\x00fake mesh"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake preview"
MODEL_URL = "https://cdn.tripo3d.ai/output/model_pbr.glb?sig=abc"
PREVIEW_URL = "https://cdn.tripo3d.ai/output/preview.png?sig=abc"


class FakeTripo:
    """A scripted Tripo: records requests, replies like the documented API."""

    def __init__(self, statuses: list[str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses = statuses or ["success"]
        self.output: dict[str, Any] = {"model_url": MODEL_URL, "rendered_image_url": PREVIEW_URL}
        self.task_type = "text_to_model"
        self.error: tuple[int, dict[str, Any]] | None = None
        self.download_status = 200

    def _next_status(self) -> str:
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def _task(self, status: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": TASK_ID,
            "type": self.task_type,
            "status": status,
            "progress": 100 if status == "success" else 40,
            "created_at": "2026-04-28T12:00:00Z",
        }
        if status == "success":
            data["output"] = self.output
            data["credits_consumed"] = 30.0
        if status == "failed":
            data["error_code"] = 2018
            data["error_message"] = "model too complex"
        return data

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "cdn.tripo3d.ai":
            body = GLB_BYTES if request.url.path.endswith(".glb") else PNG_BYTES
            return httpx.Response(self.download_status, content=body)
        if self.error is not None:
            status, body = self.error
            return httpx.Response(status, json=body)
        path = request.url.path.removeprefix("/v3")
        if path == "/files":
            return httpx.Response(200, json={"code": 0, "data": {"file_token": "file_xyz"}})
        if path in ("/generation/text-to-model", "/generation/image-to-model", "/models/convert"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": TASK_ID}})
        if path.startswith("/tasks/"):
            return httpx.Response(200, json={"code": 0, "data": self._task(self._next_status())})
        if path == "/account/balance":
            return httpx.Response(200, json={"code": 0, "data": {"balance": 41465.0, "frozen": 30}})
        return httpx.Response(404, json={"code": 4001, "status": "error", "message": "No endpoint"})


def make_server(fake: FakeTripo, workspace: Path, key: str = "tsk_test") -> TripoMcpServer:
    client = TripoClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        api_key=key,
        base_url="https://openapi.tripo3d.ai/v3",
    )
    server = TripoMcpServer(client=client, workspace_dir=workspace)
    server.poll_interval_seconds = 0
    return server


def put(workspace: Path, name: str, data: bytes = b"bytes") -> str:
    path = workspace / "input" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"input/{name}"


def body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_text_to_3d_posts_the_prompt_and_returns_the_task(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)

    result = await server.handle_text_to_3d(
        {"prompt": "a cable clip", "face_limit": 20000, "auto_size": True, "texture": False}
    )

    assert result == {"task_id": TASK_ID, "status": "queued"}
    request = fake.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openapi.tripo3d.ai/v3/generation/text-to-model"
    assert request.headers["authorization"] == "Bearer tsk_test"
    assert body(request) == {
        "prompt": "a cable clip",
        "face_limit": 20000,
        "auto_size": True,
        "texture": False,
    }


async def test_unset_options_are_not_sent(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    await server.handle_text_to_3d({"prompt": "a vase", "negative_prompt": "  "})
    assert body(fake.requests[0]) == {"prompt": "a vase"}


async def test_overlong_prompt_is_refused_before_the_call(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="1024"):
        await server.handle_text_to_3d({"prompt": "x" * 1025})
    assert fake.requests == []


async def test_option_types_are_checked(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)
    with pytest.raises(ValueError, match="'texture' must be true or false"):
        await server.handle_text_to_3d({"prompt": "a", "texture": "yes"})
    with pytest.raises(ValueError, match="'face_limit' must be a number"):
        await server.handle_text_to_3d({"prompt": "a", "face_limit": True})
    with pytest.raises(ValueError, match="'face_limit' must be positive"):
        await server.handle_text_to_3d({"prompt": "a", "face_limit": -5})


async def test_image_to_3d_uploads_a_workspace_image_first(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "sketch.png", PNG_BYTES)

    result = await server.handle_image_to_3d({"file_path": rel})

    upload, create = fake.requests
    assert upload.url.path == "/v3/files"
    assert b'filename="sketch.png"' in upload.content
    assert PNG_BYTES in upload.content
    assert create.url.path == "/v3/generation/image-to-model"
    assert body(create) == {"input": "file_xyz"}
    assert result["task_id"] == TASK_ID


async def test_image_to_3d_passes_a_public_url_through(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    await server.handle_image_to_3d({"image_url": "https://example.com/p.png"})
    assert len(fake.requests) == 1
    assert body(fake.requests[0]) == {"input": "https://example.com/p.png"}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "exactly one"),
        ({"file_path": "a.png", "image_url": "https://x/y.png"}, "exactly one"),
        ({"image_url": "http://example.com/p.png"}, "https://"),
        ({"file_path": "../outside.png"}, "outside the adapter workspace"),
        ({"file_path": "input/notes.txt"}, "is not one of"),
        ({"file_path": "input/missing.png"}, "no file"),
    ],
)
async def test_image_to_3d_refuses_bad_sources(
    tmp_path: Path, arguments: dict[str, Any], message: str
) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path / "ws")
    with pytest.raises(ValueError, match=message):
        await server.handle_image_to_3d(arguments)
    assert fake.requests == []


async def test_an_empty_image_is_refused(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)
    rel = put(tmp_path, "blank.png", b"")
    with pytest.raises(ValueError, match="empty"):
        await server.handle_image_to_3d({"file_path": rel})


async def test_convert_from_a_task_sends_the_upper_case_format(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)

    await server.handle_convert(
        {"task_id": TASK_ID, "format": "3mf", "flatten_bottom": True, "face_limit": 50000}
    )

    request = fake.requests[0]
    assert request.url.path == "/v3/models/convert"
    assert body(request) == {
        "input": TASK_ID,
        "format": "3MF",
        "flatten_bottom": True,
        "face_limit": 50000,
    }


async def test_convert_from_a_workspace_mesh_uploads_it(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "figure.glb", GLB_BYTES)

    await server.handle_convert({"file_path": rel, "format": "stl"})

    upload, create = fake.requests
    assert upload.url.path == "/v3/files"
    assert body(create) == {"input": "file_xyz", "format": "STL"}


async def test_convert_refuses_an_unknown_format_or_two_sources(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="unsupported format"):
        await server.handle_convert({"task_id": TASK_ID, "format": "step"})
    with pytest.raises(ValueError, match="exactly one"):
        await server.handle_convert({"task_id": TASK_ID, "file_path": "a.glb", "format": "stl"})
    with pytest.raises(ValueError, match="not a Tripo task id"):
        await server.handle_convert({"task_id": "../etc", "format": "stl"})
    assert fake.requests == []


def test_convert_formats_are_validated() -> None:
    assert validate_convert_format(".STL") == "stl"
    with pytest.raises(TripoError):
        validate_convert_format("step")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def test_insufficient_credits_names_the_reason(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.error = (
        403,
        {
            "code": 2010,
            "status": "error",
            "message": "You don't have enough credit to create this task",
            "suggestion": "Please purchase more credit",
        },
    )
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="not have enough credits.*code 2010.*purchase more"):
        await server.handle_text_to_3d({"prompt": "a gear"})


async def test_a_refused_key_mentions_the_region(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.error = (401, {"code": 1000, "status": "error", "message": "Invalid API Key"})
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="other region"):
        await server.handle_get_balance({})


async def test_a_non_zero_code_on_http_200_is_still_an_error(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.error = (200, {"code": 2002, "status": "error", "message": "Unsupported parameter"})
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="Unsupported parameter"):
        await server.handle_text_to_3d({"prompt": "a gear"})


async def test_unreachable_api_is_an_error(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = TripoClient(client=httpx.AsyncClient(transport=httpx.MockTransport(boom)), api_key="k")
    server = TripoMcpServer(client=client, workspace_dir=tmp_path)
    with pytest.raises(ValueError, match="could not reach Tripo"):
        await server.handle_text_to_3d({"prompt": "a gear"})


async def test_missing_key_is_refused_without_a_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    fake = FakeTripo()
    server = make_server(fake, tmp_path, key="")
    with pytest.raises(ValueError, match="TRIPO_API_KEY is unset"):
        await server.handle_get_balance({})
    assert fake.requests == []


def test_base_url_defaults_to_global_and_can_be_overridden(monkeypatch) -> None:
    monkeypatch.delenv("TRIPO_BASE_URL", raising=False)
    assert TripoClient(api_key="k")._base_url == "https://openapi.tripo3d.ai/v3"
    monkeypatch.setenv("TRIPO_BASE_URL", "https://openapi.tripo3d.com/v3/")
    assert TripoClient(api_key="k")._base_url == "https://openapi.tripo3d.com/v3"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def test_get_task_waits_until_done(tmp_path: Path) -> None:
    fake = FakeTripo(statuses=["queued", "running", "success"])
    server = make_server(fake, tmp_path)

    result = await server.handle_get_task({"task_id": TASK_ID})

    assert result["status"] == "success"
    assert result["progress"] == 100
    assert result["outputs"] == ["model_url", "rendered_image_url"]
    assert result["credits_consumed"] == 30.0
    assert len(fake.requests) == 3


async def test_get_task_without_wait_returns_the_running_status(tmp_path: Path) -> None:
    fake = FakeTripo(statuses=["running", "success"])
    server = make_server(fake, tmp_path)
    result = await server.handle_get_task({"task_id": TASK_ID, "wait_seconds": 0})
    assert result["status"] == "running"
    assert len(fake.requests) == 1


async def test_get_task_reports_tripos_failure_reason(tmp_path: Path) -> None:
    server = make_server(FakeTripo(statuses=["failed"]), tmp_path)
    result = await server.handle_get_task({"task_id": TASK_ID, "wait_seconds": 0})
    assert result["status"] == "failed"
    assert result["error"] == "model too complex"


async def test_a_banned_task_says_why(tmp_path: Path) -> None:
    server = make_server(FakeTripo(statuses=["banned"]), tmp_path)
    result = await server.handle_get_task({"task_id": TASK_ID})
    assert result["status"] == "banned"
    assert "content policy" in result["error"]


async def test_get_task_rejects_a_bad_wait(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)
    with pytest.raises(ValueError, match="wait_seconds"):
        await server.handle_get_task({"task_id": TASK_ID, "wait_seconds": 500})


async def test_get_balance(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)
    assert await server.handle_get_balance({}) == {"balance": 41465.0, "frozen": 30.0}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def test_download_writes_the_mesh_into_the_workspace(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)

    result = await server.handle_download({"task_id": TASK_ID})

    assert result["file_path"] == f"output/tripo/{TASK_ID}.glb"
    assert result["format"] == "glb"
    assert (tmp_path / result["file_path"]).read_bytes() == GLB_BYTES
    assert len(result["files"]) == 1


async def test_download_can_include_the_preview(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)
    result = await server.handle_download({"task_id": TASK_ID, "include_preview": True})
    preview = result["files"][1]
    assert preview["kind"] == "preview"
    assert preview["file_path"] == f"output/tripo/{TASK_ID}_preview.png"
    assert (tmp_path / preview["file_path"]).read_bytes() == PNG_BYTES


async def test_a_converted_file_keeps_its_format(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.task_type = "convert"
    fake.output = {"model_url": "https://cdn.tripo3d.ai/output/model.stl"}
    server = make_server(fake, tmp_path)

    # The fake serves preview bytes for any non-.glb path;
    # what matters here is the name and format.
    result = await server.handle_download({"task_id": TASK_ID})
    assert result["file_path"] == f"output/tripo/{TASK_ID}.stl"
    assert result["format"] == "stl"


async def test_a_generation_url_with_no_suffix_is_saved_as_glb(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.output = {"model_url": "https://cdn.tripo3d.ai/output/abcdef"}
    server = make_server(fake, tmp_path)
    result = await server.handle_download({"task_id": TASK_ID})
    assert result["format"] == "glb"


async def test_a_conversion_url_with_an_unknown_suffix_is_refused(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.task_type = "convert"
    fake.output = {"model_url": "https://cdn.tripo3d.ai/output/model.exe"}
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="cannot tell the format"):
        await server.handle_download({"task_id": TASK_ID})
    assert not (tmp_path / "output").exists()


async def test_download_refuses_an_unfinished_task(tmp_path: Path) -> None:
    server = make_server(FakeTripo(statuses=["running"]), tmp_path)
    with pytest.raises(ValueError, match="not success"):
        await server.handle_download({"task_id": TASK_ID})


async def test_expired_download_url_leaves_no_partial_file(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.download_status = 403
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="expired"):
        await server.handle_download({"task_id": TASK_ID})
    out = tmp_path / "output" / "tripo"
    assert not any(out.iterdir())


async def test_download_refuses_plain_http(tmp_path: Path) -> None:
    fake = FakeTripo()
    fake.output = {"model_url": "http://cdn.tripo3d.ai/output/model.glb"}
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="non-https"):
        await server.handle_download({"task_id": TASK_ID})


async def test_task_id_cannot_escape_the_workspace(tmp_path: Path) -> None:
    fake = FakeTripo()
    server = make_server(fake, tmp_path)
    for bad in ("../../etc/passwd", "a/b", "task id"):
        with pytest.raises(ValueError, match="not a Tripo task id"):
            await server.handle_download({"task_id": bad})
    assert fake.requests == []


# ---------------------------------------------------------------------------
# Registration and policy
# ---------------------------------------------------------------------------


def test_registers_six_tools(tmp_path: Path) -> None:
    server = make_server(FakeTripo(), tmp_path)
    assert sorted(server.tool_ids) == [
        "tripo.convert",
        "tripo.download",
        "tripo.get_balance",
        "tripo.get_task",
        "tripo.image_to_3d",
        "tripo.text_to_3d",
    ]


def test_spending_tools_are_writes_and_reads_are_read_only() -> None:
    for tool_id in ("tripo.text_to_3d", "tripo.image_to_3d", "tripo.convert", "tripo.download"):
        hints = annotations_for(tool_id)
        assert hints["readOnlyHint"] is False
        assert hints["destructiveHint"] is False
        assert hints["openWorldHint"] is True
    for tool_id in ("tripo.get_task", "tripo.get_balance"):
        hints = annotations_for(tool_id)
        assert hints["readOnlyHint"] is True
        assert hints["openWorldHint"] is True


def test_dashboard_chat_holds_every_credit_spending_call_for_approval() -> None:
    from api_gateway.chat.harness_backend import _REQUIRES_APPROVAL_TOOL_IDS

    for tool_id in ("tripo.text_to_3d", "tripo.image_to_3d", "tripo.convert"):
        assert tool_id in _REQUIRES_APPROVAL_TOOL_IDS
    for tool_id in ("tripo.get_task", "tripo.get_balance", "tripo.download"):
        assert tool_id not in _REQUIRES_APPROVAL_TOOL_IDS


async def test_bootstrap_registers_tripo_only_with_a_key(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    registry = await bootstrap_tool_registry(adapter_ids=["tripo"])
    assert registry.list_adapters() == []

    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    registry = await bootstrap_tool_registry(adapter_ids=["tripo"])
    assert [a.adapter_id for a in registry.list_adapters()] == ["tripo"]


async def test_bootstrap_honours_the_per_adapter_toggle(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    monkeypatch.setenv("METAFORGE_ADAPTER_TRIPO_ENABLED", "false")
    registry = await bootstrap_tool_registry(adapter_ids=["tripo"])
    assert registry.list_adapters() == []
