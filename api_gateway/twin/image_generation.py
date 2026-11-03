"""Image generation providers for illustrative concept images (FORGE-761).

The smallest abstraction that fits: one async callable

    generate(prompt, conditioning, size) -> GeneratedImage

where ``conditioning`` is an optional PNG the model should follow (the block
layout drawn by the concept sketch renderer) and ``GeneratedImage`` carries
the bytes, their MIME type, and which provider and model made them, so the
twin can say what produced a picture.

One real implementation, OpenAI's images API, chosen by environment:

* ``METAFORGE_IMAGE_PROVIDER``: ``openai`` turns it on. Unset, ``off`` or
  ``none`` means no provider. Generation costs money per image, so nothing
  is enabled just because an API key happens to be present.
* ``METAFORGE_IMAGE_MODEL``: the model (default ``gpt-image-1``, which takes
  an input image on its edits endpoint, so the layout can condition it).
* ``METAFORGE_IMAGE_TIMEOUT_S``: seconds before a request is abandoned
  (default 120).
* The key is ``OPENAI_API_KEY``, the same variable every other OpenAI caller
  in MetaForge reads. Its value is never logged or returned.

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

DEFAULT_OPENAI_MODEL = "gpt-image-1"
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


_KEY_LIKE = re.compile(r"\b(sk|key)-[A-Za-z0-9_*\-]{4,}")


def _describe(exc: BaseException) -> str:
    """A provider error, safe to log and return: never a key, nor part of one."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    head = f"{name} (HTTP {status})" if status else name
    if "Authentication" in name or "Permission" in name:
        return f"{head}: the API key was not accepted"
    return f"{head}: {_KEY_LIKE.sub('[redacted]', str(exc))[:400]}"


def resolve_image_generator(env: Mapping[str, str] | None = None) -> ImageGenerator:
    """The configured provider, or :class:`ImageGenerationUnavailableError` saying why."""
    env = os.environ if env is None else env
    provider = (env.get(PROVIDER_ENV) or "").strip().lower()
    if provider in _OFF:
        raise ImageGenerationUnavailableError(
            f"no image generation provider is configured: set {PROVIDER_ENV}=openai "
            f"(and {OPENAI_KEY_ENV}) to enable AI concept images"
        )
    if provider != "openai":
        raise ImageGenerationUnavailableError(
            f"{PROVIDER_ENV}={provider!r} is not a known image provider; supported: openai"
        )
    api_key = (env.get(OPENAI_KEY_ENV) or "").strip()
    if not api_key:
        raise ImageGenerationUnavailableError(
            f"{PROVIDER_ENV}=openai but {OPENAI_KEY_ENV} is not set"
        )
    model = (env.get(MODEL_ENV) or "").strip() or DEFAULT_OPENAI_MODEL
    return OpenAIImageGenerator(model=model, api_key=api_key, timeout_s=timeout_from_env(env))


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
