"""Images in tool results reach the model as images, once (2026-11-14).

``twin.imagine_look`` returns studio screenshots as base64 in its JSON. They
used to be rendered as text, turned into a result handle, and never seen by
the model. These tests drive the native loop through the real provider
adapters (OpenAI-compatible and Anthropic) with fake SDK clients and check
what the model request actually carries. Network-free.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.harness import HarnessRuntime
from orchestrator.harness.native_tools import run_native_tools
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.providers.adapters import anthropic_invoke, openai_invoke
from orchestrator.harness.providers.images import (
    TOOL_IMAGES_KEY,
    anthropic_tool_result_content,
    openai_with_tool_images,
)
from orchestrator.harness.tool_images import (
    IMAGES_ENV,
    MAX_IMAGES_PER_STEP,
    ImageBudget,
    expire_tool_images,
    extract_images,
)
from orchestrator.harness.tools import ToolRegistry

CONFIG = load_provider_config({"roles": {"generator": [{"provider": "openai", "model": "gpt-4o"}]}})
OR_SPEC = ProviderSpec(
    name="openrouter",
    model="anthropic/claude-sonnet-5.5",
    base_url="https://openrouter.ai/api/v1",
    api_key="k",
)
ANTHROPIC_SPEC = ProviderSpec(name="anthropic", model="claude-x", api_key="k")

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _png(fill: int, size: int = 30_000) -> str:
    return base64.b64encode(PNG_HEADER + bytes([fill]) * size).decode("ascii")


def _look_result(n: int = 2) -> dict[str, Any]:
    views = ["first look", "after the change"] + [f"view {i}" for i in range(2, n)]
    return {
        "project_id": "p1",
        "revision": 3,
        "status": "ok",
        "size": [120.0, 80.0, 30.0],
        "parts": ["shell", "lid"],
        "errors": [],
        "measured": {"volume_mm3": 1234.5},
        "images": [{"view": views[i], "png_base64": _png(i + 1)} for i in range(n)],
    }


def _runtime(n_images: int = 2) -> HarnessRuntime:
    tools = ToolRegistry()

    async def look(_args: dict[str, Any]) -> dict[str, Any]:
        return _look_result(n_images)

    async def double(args: dict[str, Any]) -> dict[str, Any]:
        return {"result": args["x"] * 2}

    tools.register_native(
        "imagine_look", description="look", input_schema={"type": "object"}, handler=look
    )
    tools.register_native(
        "double",
        description="double",
        input_schema={"type": "object", "properties": {"x": {"type": "number"}}},
        handler=double,
    )
    return HarnessRuntime.build(CONFIG, tools=tools)


# -- fake SDK clients ----------------------------------------------------------------


def _openai_reply(text: str, calls: list[tuple[str, str, dict[str, Any]]]) -> Any:
    tcs = [
        SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for cid, name, args in calls
    ]
    msg = SimpleNamespace(content=text, tool_calls=tcs or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="m")


class _OpenAIClient:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]


def _anthropic_reply(text: str, calls: list[tuple[str, str, dict[str, Any]]]) -> Any:
    blocks: list[Any] = [SimpleNamespace(type="text", text=text)] if text else []
    blocks += [
        SimpleNamespace(type="tool_use", id=cid, name=name, input=args) for cid, name, args in calls
    ]
    return SimpleNamespace(content=blocks, model="claude-x", stop_reason="end_turn")


class _AnthropicClient:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]


def _via_openai(client: _OpenAIClient) -> Any:
    async def invoke(_spec: ProviderSpec, request: Any) -> dict[str, Any]:
        return await openai_invoke(OR_SPEC, request, client=client)

    return invoke


def _via_anthropic(client: _AnthropicClient) -> Any:
    async def invoke(_spec: ProviderSpec, request: Any) -> dict[str, Any]:
        return await anthropic_invoke(ANTHROPIC_SPEC, request, client=client)

    return invoke


def _image_parts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        p
        for m in messages
        if isinstance(m.get("content"), list)
        for p in m["content"]
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]


# -- (1) the look reaches the model as images ----------------------------------------


@pytest.mark.asyncio
async def test_openai_compatible_request_gets_image_parts_and_no_base64_text() -> None:
    client = _OpenAIClient(
        [
            _openai_reply("", [("c1", "imagine_look", {})]),
            _openai_reply("It looks balanced.", []),
        ]
    )
    res = await run_native_tools(_runtime(), "how does it look?", invoke=_via_openai(client))
    assert res.output == "It looks balanced."

    messages = client.requests[1]["messages"]
    tool_idx = next(i for i, m in enumerate(messages) if m.get("role") == "tool")
    tool_msg = messages[tool_idx]
    assert TOOL_IMAGES_KEY not in tool_msg  # OpenAI rejects unknown keys on a message
    content = tool_msg["content"]
    assert _png(1) not in content and _png(2) not in content
    assert "result_handle" not in content  # the rest stays inline
    body = json.loads(content)
    assert body["status"] == "ok" and body["measured"] == {"volume_mm3": 1234.5}
    assert body["images"][0]["png_base64"].startswith("[image: first look, attached")

    follow = messages[tool_idx + 1]
    assert follow["role"] == "user"
    assert "first look; after the change" in follow["content"][0]["text"]
    urls = [p["image_url"]["url"] for p in _image_parts(messages)]
    assert urls == [f"data:image/png;base64,{_png(1)}", f"data:image/png;base64,{_png(2)}"]

    # The trace keeps the full observation, base64 included.
    assert res.steps[0].observation["images"][0]["png_base64"] == _png(1)


@pytest.mark.asyncio
async def test_anthropic_request_puts_image_blocks_inside_the_tool_result() -> None:
    client = _AnthropicClient(
        [
            _anthropic_reply("", [("t1", "imagine_look", {})]),
            _anthropic_reply("Looks good.", []),
        ]
    )
    res = await run_native_tools(_runtime(), "look", invoke=_via_anthropic(client))
    assert res.output == "Looks good."

    last = client.requests[1]["messages"][-1]
    assert last["role"] == "user"
    result = last["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
    blocks = result["content"]
    assert blocks[0]["type"] == "text" and _png(1) not in blocks[0]["text"]
    images = [b for b in blocks if b["type"] == "image"]
    assert [b["source"] for b in images] == [
        {"type": "base64", "media_type": "image/png", "data": _png(1)},
        {"type": "base64", "media_type": "image/png", "data": _png(2)},
    ]


# -- (2) images do not stay in later steps -------------------------------------------


@pytest.mark.asyncio
async def test_images_are_sent_once_then_only_the_placeholder_stays() -> None:
    client = _OpenAIClient(
        [
            _openai_reply("", [("c1", "imagine_look", {})]),
            _openai_reply("Now doubling.", [("c2", "double", {"x": 2})]),
            _openai_reply("Done.", []),
        ]
    )
    await run_native_tools(_runtime(), "look then double", invoke=_via_openai(client))
    assert len(client.requests) == 3
    assert len(_image_parts(client.requests[1]["messages"])) == 2
    later = client.requests[2]["messages"]
    assert _image_parts(later) == []
    assert _png(1) not in json.dumps(later)
    look_msg = next(m for m in later if m.get("tool_call_id") == "c1")
    assert "[image: first look" in look_msg["content"]


@pytest.mark.asyncio
async def test_at_most_the_step_cap_of_images_is_attached() -> None:
    client = _OpenAIClient(
        [_openai_reply("", [("c1", "imagine_look", {})]), _openai_reply("ok", [])]
    )
    await run_native_tools(_runtime(n_images=6), "look", invoke=_via_openai(client))
    messages = client.requests[1]["messages"]
    assert len(_image_parts(messages)) == MAX_IMAGES_PER_STEP
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg["content"].count("not attached: only") == 6 - MAX_IMAGES_PER_STEP


def test_expire_tool_images_keeps_only_the_batch_after_the_last_assistant_turn() -> None:
    img = [{"media_type": "image/png", "data": "AAAA", "label": "x"}]
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": "a", "content": "old", TOOL_IMAGES_KEY: img},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": "b", "content": "new", TOOL_IMAGES_KEY: img},
    ]
    out = expire_tool_images(messages)
    assert TOOL_IMAGES_KEY not in out[2] and out[2]["content"] == "old"
    assert out[4][TOOL_IMAGES_KEY] == img
    assert TOOL_IMAGES_KEY in messages[2]  # not changed in place
    assert expire_tool_images(out) is out


# -- (3) results without images are unaffected ----------------------------------------


@pytest.mark.asyncio
async def test_a_result_without_images_renders_exactly_as_before() -> None:
    client = _OpenAIClient(
        [_openai_reply("", [("c1", "double", {"x": 21})]), _openai_reply("42", [])]
    )
    await run_native_tools(_runtime(), "double 21", invoke=_via_openai(client))
    messages = client.requests[1]["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg == {"role": "tool", "tool_call_id": "c1", "content": '{"result": 42}'}
    assert messages[-1] is not None and messages[-1]["role"] == "tool"
    assert _image_parts(messages) == []


def test_provider_renderers_leave_plain_messages_alone() -> None:
    messages = [{"role": "tool", "tool_call_id": "c", "content": "x"}]
    assert openai_with_tool_images(messages) is messages
    assert anthropic_tool_result_content(messages[0]) == "x"


def test_extract_images_returns_a_value_without_images_untouched() -> None:
    value = {"status": "ok", "base64_note": "no image here", "items": [1, 2]}
    assert extract_images(value, ImageBudget()) == (value, [])
    assert extract_images("plain text", ImageBudget()) == ("plain text", [])


# -- shapes and limits ---------------------------------------------------------------


def test_mcp_image_block_and_data_url_in_a_json_string_are_recognised() -> None:
    jpeg = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 50).decode()
    raw = json.dumps(
        {
            "content": [
                {"type": "image", "data": _png(1, 10), "mimeType": "image/png"},
                {"type": "text", "text": "hi"},
            ],
            "shot": {"label": "top", "image_base64": f"data:image/jpeg;base64,{jpeg}"},
        }
    )
    stripped, images = extract_images(raw, ImageBudget())
    assert [i.media_type for i in images] == ["image/png", "image/jpeg"]
    assert images[1].label == "top" and images[1].data == jpeg
    assert "base64" not in stripped["content"][0]["data"]
    assert stripped["content"][1] == {"type": "text", "text": "hi"}


def test_oversized_invalid_and_unsupported_images_are_not_attached() -> None:
    value = {
        "a": {"view": "big", "png_base64": _png(1, 5000)},
        "b": {"view": "junk", "png_base64": "not base64!!"},
        "c": {"view": "svg", "image_base64": base64.b64encode(b"<svg/>").decode()},
    }
    stripped, images = extract_images(value, ImageBudget(max_bytes=1000))
    assert images == []
    assert "over the" in stripped["a"]["png_base64"]
    assert "not valid base64" in stripped["b"]["png_base64"]
    assert "unsupported image type" in stripped["c"]["image_base64"]


def test_turning_images_off_still_takes_the_base64_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(IMAGES_ENV, "off")
    stripped, images = extract_images(_look_result(), ImageBudget())
    assert images == []
    assert "turned off" in stripped["images"][0]["png_base64"]
    assert _png(1) not in json.dumps(stripped)
