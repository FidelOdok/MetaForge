"""Image generation providers for illustrative concept images (FORGE-761).

The smallest abstraction that fits: one async callable

    generate(prompt, conditioning, size) -> GeneratedImage

where ``conditioning`` is an optional PNG the model should follow (the block
layout drawn by the concept sketch renderer) and ``GeneratedImage`` carries
the bytes, their MIME type, and which provider and model made them, so the
twin can say what produced a picture.

Two real implementations, chosen by environment:

* ``METAFORGE_IMAGE_PROVIDER``: ``openrouter`` or ``openai`` turns one on.
  Unset, ``off`` or ``none`` means no provider. Generation costs money per
  image, so nothing is enabled just because an API key happens to be present.
* ``openrouter`` (FORGE-769): OpenRouter's chat completions with the image
  output modality, on the key the deployment already has: ``OPENROUTER_API_KEY``,
  ``OPEN_ROUTER_API_KEY``, or ``METAFORGE_LLM_API_KEY`` when OpenRouter is the
  gateway's LLM provider. The
  layout PNG goes in the message beside the prompt, and the image comes back
  in the reply, so GPT Image or Google's Gemini image model is a model slug.
* ``openai``: OpenAI's images API on ``OPENAI_API_KEY`` (``images.edit``
  with the layout, else ``images.generate``).
* ``METAFORGE_IMAGE_MODEL``: the model (default ``google/gemini-2.5-flash-image``
  on OpenRouter, ``gpt-image-1`` on OpenAI).
* ``METAFORGE_IMAGE_TIMEOUT_S``: seconds before a request is abandoned
  (default 120).
* Key values are never logged or returned.

No provider configured is :class:`ImageGenerationUnavailableError`, raised at
once; a provider that does not answer in time is
:class:`ImageGenerationTimeoutError`. Neither ever hangs a tool call.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.image_generation")

PROVIDER_ENV = "METAFORGE_IMAGE_PROVIDER"
MODEL_ENV = "METAFORGE_IMAGE_MODEL"
TIMEOUT_ENV = "METAFORGE_IMAGE_TIMEOUT_S"
OPENAI_KEY_ENV = "OPENAI_API_KEY"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
#: The same endpoint the harness provider registry uses for OpenRouter.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_OPENAI_MODEL = "gpt-image-1"
#: Google's Gemini image model ("Nano Banana"): it takes an input image, so
#: the layout can condition it.
DEFAULT_OPENROUTER_MODEL = "google/gemini-2.5-flash-image"
#: The tool's sizes as the aspect ratios OpenRouter's image models take.
_ASPECT_RATIOS = {"1024x1024": "1:1", "1536x1024": "3:2", "1024x1536": "2:3"}
DEFAULT_TIMEOUT_S = 120.0
#: The sizes gpt-image-1 accepts; the tool offers exactly these.
SIZES: tuple[str, ...] = ("1024x1024", "1536x1024", "1024x1536")
_OFF = {"", "off", "none", "disabled", "false", "0"}


class ImageGenerationError(RuntimeError):
    """Image generation did not produce an image."""


class ImageGenerationUnavailableError(ImageGenerationError):
    """No image generation provider is configured, or it cannot be used."""


class ImageGenerationTimeoutError(ImageGenerationError):
    """The provider did not answer within the timeout."""


@dataclass(frozen=True)
class GeneratedImage:
    """One generated image and what made it."""

    image_bytes: bytes
    mime: str
    provider: str
    model: str
    #: Whether the conditioning image actually reached the model.
    conditioned: bool = False


#: ``(prompt, conditioning_png | None, size) -> GeneratedImage``
ImageGenerator = Callable[[str, bytes | None, str], Awaitable[GeneratedImage]]


def parse_size(size: str) -> tuple[int, int]:
    """``"1536x1024"`` -> ``(1536, 1024)``; anything not in :data:`SIZES` is refused."""
    if size not in SIZES:
        raise ValueError(f"size must be one of {', '.join(SIZES)}, got {size!r}")
    w, h = size.split("x")
    return int(w), int(h)


def timeout_from_env(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(TIMEOUT_ENV) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning("image_generation_timeout_invalid", value=raw)
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


class OpenAIImageGenerator:
    """OpenAI's images API: ``images.edit`` with the layout, else ``images.generate``."""

    provider = "openai"

    def __init__(self, *, model: str, api_key: str, timeout_s: float) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def __call__(self, prompt: str, conditioning: bytes | None, size: str) -> GeneratedImage:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - the gateway image has it
            raise ImageGenerationUnavailableError(
                "the openai package is not installed, so no image can be generated"
            ) from exc

        client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout_s, max_retries=0)
        with tracer.start_as_current_span("image_generation.openai") as span:
            span.set_attribute("image_generation.model", self.model)
            span.set_attribute("image_generation.conditioned", conditioning is not None)
            try:
                response: Any
                if conditioning is not None:
                    response = await client.images.edit(
                        model=self.model,
                        image=("layout.png", conditioning, "image/png"),
                        prompt=prompt,
                        size=size,  # type: ignore[arg-type]
                    )
                else:
                    response = await client.images.generate(
                        model=self.model,
                        prompt=prompt,
                        size=size,  # type: ignore[arg-type]
                    )
            except Exception as exc:
                span.record_exception(exc)
                if type(exc).__name__ in {"APITimeoutError", "TimeoutError"}:
                    raise ImageGenerationTimeoutError(
                        f"openai {self.model} did not answer in {self._timeout_s:g}s"
                    ) from exc
                raise ImageGenerationError(
                    f"openai {self.model} refused the request: {_describe(exc)}"
                ) from exc
            finally:
                await client.close()

        data = getattr(response, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if not b64:
            raise ImageGenerationError(f"openai {self.model} returned no image data")
        return GeneratedImage(
            image_bytes=base64.b64decode(b64),
            mime="image/png",
            provider=self.provider,
            model=self.model,
            conditioned=conditioning is not None,
        )


class OpenRouterImageGenerator:
    """OpenRouter chat completions with the image output modality (FORGE-769).

    The gateway already talks to OpenRouter for chat; image models on it
    answer a chat message with images in the reply rather than through an
    images endpoint. The layout PNG rides in the message as an image part.
    """

    provider = "openrouter"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_s: float,
        base_url: str = OPENROUTER_BASE_URL,
        transport: Any = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._base_url = base_url.rstrip("/")
        # Injectable for tests (an ``httpx.MockTransport``); real calls use httpx's own.
        self._transport = transport

    def _body(self, prompt: str, conditioning: bytes | None, size: str) -> dict[str, Any]:
        text = f"{prompt}\n\nAspect ratio {_ASPECT_RATIOS.get(size, '1:1')}."
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if conditioning is not None:
            data = base64.b64encode(conditioning).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}
            )
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
            "image_config": {"aspect_ratio": _ASPECT_RATIOS.get(size, "1:1")},
        }

    async def __call__(self, prompt: str, conditioning: bytes | None, size: str) -> GeneratedImage:
        import httpx

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Title": "MetaForge",
        }
        with tracer.start_as_current_span("image_generation.openrouter") as span:
            span.set_attribute("image_generation.model", self.model)
            span.set_attribute("image_generation.conditioned", conditioning is not None)
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_s, transport=self._transport
                ) as client:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        json=self._body(prompt, conditioning, size),
                        headers=headers,
                    )
                    if response.status_code in (401, 403):
                        raise ImageGenerationError(
                            f"openrouter {self.model} refused the request "
                            f"(HTTP {response.status_code}): the API key was not accepted"
                        )
                    if response.status_code >= 400:
                        raise ImageGenerationError(
                            f"openrouter {self.model} refused the request "
                            f"(HTTP {response.status_code}): "
                            f"{_KEY_LIKE.sub('[redacted]', response.text)[:400]}"
                        )
                    payload = response.json()
                    image_bytes, mime = await _openrouter_image(payload, client, self.model)
            except ImageGenerationError as exc:
                span.record_exception(exc)
                raise
            except httpx.TimeoutException as exc:
                span.record_exception(exc)
                raise ImageGenerationTimeoutError(
                    f"openrouter {self.model} did not answer in {self._timeout_s:g}s"
                ) from exc
            except Exception as exc:
                span.record_exception(exc)
                raise ImageGenerationError(
                    f"openrouter {self.model} failed: {_describe(exc)}"
                ) from exc
        return GeneratedImage(
            image_bytes=image_bytes,
            mime=mime,
            provider=self.provider,
            model=self.model,
            conditioned=conditioning is not None,
        )


_DATA_URL = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)


async def _openrouter_image(payload: Any, client: Any, model: str) -> tuple[bytes, str]:
    """The first image in an OpenRouter reply, as ``(bytes, mime)``."""
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ImageGenerationError(f"openrouter {model} returned no message") from exc
    for image in message.get("images") or []:
        url = str(((image or {}).get("image_url") or {}).get("url") or "")
        match = _DATA_URL.match(url)
        if match:
            return base64.b64decode(match.group(2)), match.group(1).lower()
        if url.startswith("https://"):
            fetched = await client.get(url)
            fetched.raise_for_status()
            mime = fetched.headers.get("content-type", "image/png").split(";")[0]
            return fetched.content, mime
    said = str(message.get("content") or "")[:200]
    raise ImageGenerationError(
        f"openrouter {model} returned no image"
        + (f" (it replied: {_KEY_LIKE.sub('[redacted]', said)})" if said else "")
        + "; check the model takes the image output modality"
    )


_KEY_LIKE = re.compile(r"\b(sk|key)-[A-Za-z0-9_*\-]{4,}")


def _describe(exc: BaseException) -> str:
    """A provider error, safe to log and return: never a key, nor part of one."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    head = f"{name} (HTTP {status})" if status else name
    if "Authentication" in name or "Permission" in name:
        return f"{head}: the API key was not accepted"
    return f"{head}: {_KEY_LIKE.sub('[redacted]', str(exc))[:400]}"


#: Where an OpenRouter key may already be: its own name, the spelling the
#: knowledge layer and the compose file use, and the gateway's generic LLM key
#: when OpenRouter is the gateway's LLM provider.
OPENROUTER_KEY_ALIASES: tuple[str, ...] = (OPENROUTER_KEY_ENV, "OPEN_ROUTER_API_KEY")


def _openrouter_key(env: Mapping[str, str]) -> str:
    for name in OPENROUTER_KEY_ALIASES:
        value = (env.get(name) or "").strip()
        if value:
            return value
    if (env.get("METAFORGE_LLM_PROVIDER") or "").strip().lower() == "openrouter":
        return (env.get("METAFORGE_LLM_API_KEY") or "").strip()
    return ""


def resolve_image_generator(env: Mapping[str, str] | None = None) -> ImageGenerator:
    """The configured provider, or :class:`ImageGenerationUnavailableError` saying why."""
    env = os.environ if env is None else env
    provider = (env.get(PROVIDER_ENV) or "").strip().lower()
    if provider in _OFF:
        raise ImageGenerationUnavailableError(
            f"no image generation provider is configured: set {PROVIDER_ENV}=openrouter "
            f"(with {OPENROUTER_KEY_ENV}) or openai (with {OPENAI_KEY_ENV}) to enable AI "
            "concept images"
        )
    model = (env.get(MODEL_ENV) or "").strip()
    if provider == "openrouter":
        key = _openrouter_key(env)
        if not key:
            raise ImageGenerationUnavailableError(
                f"{PROVIDER_ENV}=openrouter but {OPENROUTER_KEY_ENV} is not set"
            )
        return OpenRouterImageGenerator(
            model=model or DEFAULT_OPENROUTER_MODEL,
            api_key=key,
            timeout_s=timeout_from_env(env),
        )
    if provider != "openai":
        raise ImageGenerationUnavailableError(
            f"{PROVIDER_ENV}={provider!r} is not a known image provider; "
            "supported: openrouter, openai"
        )
    api_key = (env.get(OPENAI_KEY_ENV) or "").strip()
    if not api_key:
        raise ImageGenerationUnavailableError(
            f"{PROVIDER_ENV}=openai but {OPENAI_KEY_ENV} is not set"
        )
    return OpenAIImageGenerator(
        model=model or DEFAULT_OPENAI_MODEL, api_key=api_key, timeout_s=timeout_from_env(env)
    )


async def generate_with_timeout(
    generator: ImageGenerator,
    prompt: str,
    conditioning: bytes | None,
    size: str,
    *,
    timeout_s: float,
) -> GeneratedImage:
    """Call ``generator``, abandoning it after ``timeout_s`` seconds."""
    try:
        return await asyncio.wait_for(generator(prompt, conditioning, size), timeout=timeout_s)
    except TimeoutError as exc:
        raise ImageGenerationTimeoutError(
            f"the image provider did not answer in {timeout_s:g}s"
        ) from exc
