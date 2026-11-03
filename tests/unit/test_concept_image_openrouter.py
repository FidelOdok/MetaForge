"""Concept images through OpenRouter (FORGE-769).

The gateway's provider is OpenRouter, so the AI concept image goes through
its chat completions with the image output modality: the layout PNG rides in
the message, the picture comes back in the reply. Every call here is against
a mock transport; no image is ever paid for.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from api_gateway.twin.image_generation import (
    DEFAULT_OPENROUTER_MODEL,
    ImageGenerationError,
    ImageGenerationTimeoutError,
    ImageGenerationUnavailableError,
    OpenRouterImageGenerator,
    resolve_image_generator,
)

PNG = b"\x89PNG\r\n\x1a\nfake-image"
LAYOUT = b"\x89PNG\r\n\x1a\nlayout"
KEY = "sk-or-v1-secret-key-1234567890"


def _reply(images: list[str], content: str = "") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "images": [{"type": "image_url", "image_url": {"url": u}} for u in images],
                }
            }
        ]
    }


def _data_url(data: bytes = PNG, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class Recorder:
    """A mock OpenRouter that records what it was sent."""

    def __init__(self, handler: Any) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[0].content)


def _generator(handler: Any, model: str = DEFAULT_OPENROUTER_MODEL) -> tuple[Any, Recorder]:
    recorder = Recorder(handler)
    gen = OpenRouterImageGenerator(
        model=model, api_key=KEY, timeout_s=5, transport=httpx.MockTransport(recorder)
    )
    return gen, recorder


async def test_the_layout_and_prompt_go_in_one_message_with_the_image_modality() -> None:
    gen, sent = _generator(lambda r: httpx.Response(200, json=_reply([_data_url()])))
    image = await gen("a birch plywood chair, studio light", LAYOUT, "1536x1024")

    assert image.image_bytes == PNG and image.mime == "image/png"
    assert image.provider == "openrouter" and image.model == DEFAULT_OPENROUTER_MODEL
    assert image.conditioned is True
    request = sent.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = sent.body
    assert body["model"] == DEFAULT_OPENROUTER_MODEL
    assert body["modalities"] == ["image", "text"]
    assert body["image_config"] == {"aspect_ratio": "3:2"}
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text" and "birch plywood chair" in parts[0]["text"]
    assert parts[1]["image_url"]["url"] == _data_url(LAYOUT)


async def test_without_a_layout_only_the_prompt_is_sent() -> None:
    gen, sent = _generator(lambda r: httpx.Response(200, json=_reply([_data_url()])))
    image = await gen("a chair", None, "1024x1024")
    assert image.conditioned is False
    assert [p["type"] for p in sent.body["messages"][0]["content"]] == ["text"]


async def test_a_jpeg_reply_keeps_its_type() -> None:
    gen, _ = _generator(lambda r: httpx.Response(200, json=_reply([_data_url(mime="image/jpeg")])))
    assert (await gen("a chair", None, "1024x1024")).mime == "image/jpeg"


async def test_a_reply_with_no_image_says_what_the_model_said() -> None:
    gen, _ = _generator(lambda r: httpx.Response(200, json=_reply([], "I can only describe it")))
    with pytest.raises(ImageGenerationError, match="returned no image.*I can only describe it"):
        await gen("a chair", LAYOUT, "1024x1024")


async def test_a_rejected_key_never_appears_in_the_error() -> None:
    gen, _ = _generator(lambda r: httpx.Response(401, text=f"bad key {KEY}"))
    with pytest.raises(ImageGenerationError, match="key was not accepted") as caught:
        await gen("a chair", None, "1024x1024")
    assert "sk-or" not in str(caught.value)


async def test_a_provider_error_is_reported_with_any_key_redacted() -> None:
    gen, _ = _generator(lambda r: httpx.Response(400, text=f"model not found for {KEY}"))
    with pytest.raises(ImageGenerationError, match="HTTP 400") as caught:
        await gen("a chair", None, "1024x1024")
    assert "sk-or" not in str(caught.value) and "[redacted]" in str(caught.value)


async def test_a_timeout_is_a_timeout_error() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    gen, _ = _generator(slow)
    with pytest.raises(ImageGenerationTimeoutError, match="did not answer"):
        await gen("a chair", None, "1024x1024")


async def test_an_image_served_by_url_is_fetched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=_reply(["https://cdn.example.test/a.webp"]))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/webp"})

    gen, sent = _generator(handler)
    image = await gen("a chair", None, "1024x1024")
    assert image.image_bytes == PNG and image.mime == "image/webp"
    assert len(sent.requests) == 2


def test_openrouter_is_chosen_by_env_on_the_existing_key() -> None:
    gen = resolve_image_generator(
        {"METAFORGE_IMAGE_PROVIDER": "OpenRouter", "OPENROUTER_API_KEY": KEY}
    )
    assert isinstance(gen, OpenRouterImageGenerator)
    assert gen.model == DEFAULT_OPENROUTER_MODEL
    custom = resolve_image_generator(
        {
            "METAFORGE_IMAGE_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": KEY,
            "METAFORGE_IMAGE_MODEL": "openai/gpt-image-1",
        }
    )
    assert isinstance(custom, OpenRouterImageGenerator) and custom.model == "openai/gpt-image-1"


def test_openrouter_without_its_key_says_so() -> None:
    with pytest.raises(ImageGenerationUnavailableError, match="OPENROUTER_API_KEY is not set"):
        resolve_image_generator({"METAFORGE_IMAGE_PROVIDER": "openrouter"})


def test_an_openrouter_key_alone_does_not_turn_on_paid_generation() -> None:
    with pytest.raises(ImageGenerationUnavailableError, match="openrouter"):
        resolve_image_generator({"OPENROUTER_API_KEY": KEY})
