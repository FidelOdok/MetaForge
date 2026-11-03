"""One vision call over the gateway's model providers, shared by every reader.

Reading a sketch (``twin.interpret_sketch``) and judging a generated concept
image both send one image and a prompt to a model and want text back. This
module holds the part they share:

* :func:`vision_model`: the model vision calls use, ``METAFORGE_VISION_MODEL``.
  It is a model id for the provider chain's primary provider (on OpenRouter,
  a ``vendor/model`` slug). Unset, vision calls use the chat chain's model.
* :func:`vision_provider_config`: the chat provider chain with that model.
* :func:`make_vision_call`: an ``(image, mime_type, prompt) -> VisionReply``
  call over that chain, less every provider family whose adapter flattens a
  message to text (an image would never reach the model), bounded by a
  timeout so a silent provider is an error and not a hang.

The reply names the provider and model that answered, so a reader can record
who looked at the image.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.vision")

__all__ = [
    "VISION_MODEL_ENV",
    "VisionCall",
    "VisionReply",
    "VisionUnavailableError",
    "make_vision_call",
    "vision_candidates",
    "vision_content",
    "vision_model",
    "vision_provider_config",
]

#: The model vision calls use; unset means the chat chain's model.
VISION_MODEL_ENV = "METAFORGE_VISION_MODEL"
DEFAULT_TIMEOUT_S = 120.0


class VisionUnavailableError(RuntimeError):
    """No vision model could be asked, or none answered in time."""


@dataclass(frozen=True)
class VisionReply:
    """What a vision call returns: the text, and who answered."""

    text: str
    provider: str | None = None
    model: str | None = None


#: ``(image_bytes, mime_type, prompt) -> reply``. A plain string is accepted
#: as the reply text with no provenance.
VisionCall = Callable[[bytes, str, str], Awaitable["VisionReply | str"]]

#: Provider families whose adapter passes a message's content list through
#: unchanged. Gemini, Bedrock and Codex flatten messages to text, so an image
#: would never reach the model.
_ANTHROPIC_FAMILY = frozenset({"anthropic", "claude"})
_TEXT_ONLY_FAMILIES = frozenset(
    {"gemini", "google", "vertex", "bedrock", "aws-bedrock", "openai-codex", "codex"}
)


def vision_model() -> str | None:
    """``METAFORGE_VISION_MODEL``, or ``None`` to use the chat chain's model."""
    return (os.environ.get(VISION_MODEL_ENV) or "").strip() or None


def vision_provider_config() -> Any:
    """The chat provider chain, with :func:`vision_model` as its model when set.

    The model is applied as a per-call model override, the same way a chat
    turn's model picker is: the primary provider (and any stored provider
    that can serve it) runs it, and the env default provider stays in the
    chain with its own model as the fallback.
    """
    from api_gateway.chat.harness_backend import provider_config_from_env

    model = vision_model()
    return provider_config_from_env(model=model) if model else provider_config_from_env()


def vision_candidates(config: Any) -> tuple[list[Any], list[Any]]:
    """``(usable, all)``: the chain's specs, and those that can carry an image."""
    candidates = list(config.slots.candidates("generator"))
    usable = [s for s in candidates if s.name.lower() not in _TEXT_ONLY_FAMILIES]
    return usable, candidates


def vision_content(family: str, image: bytes, mime_type: str, prompt: str) -> list[dict[str, Any]]:
    """One user message's content: the image in the provider's own block, then the prompt."""
    data = base64.b64encode(image).decode("ascii")
    if family in _ANTHROPIC_FAMILY:
        image_block: dict[str, Any] = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": data},
        }
    else:
        image_block = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{data}"},
        }
    return [image_block, {"type": "text", "text": prompt}]


def make_vision_call(
    *,
    role: str = "vision",
    purpose: str = "reading an image",
    invoke: Any = None,
    provider_config: Any = None,
    timeout_s: float | None = None,
    max_tokens: int = 4096,
    unavailable: type[VisionUnavailableError] = VisionUnavailableError,
) -> VisionCall:
    """A :data:`VisionCall` over the gateway's configured model providers.

    ``role`` attributes the call's usage (``usage_scope``); ``purpose`` words
    the errors. ``invoke`` and ``provider_config`` are injectable for tests;
    by default the chain is :func:`vision_provider_config`. Every failure to
    reach a model raises ``unavailable`` (a :class:`VisionUnavailableError`).
    """

    async def call(image: bytes, mime_type: str, prompt: str) -> VisionReply:
        from orchestrator.harness.providers import (
            AllProvidersFailedError,
            ProviderPipeline,
            RoleModelSlots,
            default_invoke,
        )
        from orchestrator.harness.providers.usage import usage_scope

        requested = vision_model()
        with tracer.start_as_current_span("vision.call") as span:
            span.set_attribute("vision.role", role)
            span.set_attribute("vision.requested_model", requested or "")
            try:
                config = (
                    provider_config if provider_config is not None else vision_provider_config()
                )
                usable, candidates = vision_candidates(config)
            except Exception as exc:  # noqa: BLE001 - reported as unavailable, never a crash
                span.record_exception(exc)
                raise unavailable(f"no model provider is configured for {purpose}: {exc}") from exc
            if not usable:
                names = ", ".join(sorted({s.name for s in candidates})) or "none"
                raise unavailable(
                    "no vision-capable model provider is configured (configured: "
                    f"{names}); {purpose} needs Anthropic or an OpenAI-compatible "
                    "provider with a vision model"
                )
            span.set_attribute("vision.primary_provider", usable[0].name)
            span.set_attribute("vision.primary_model", usable[0].model)
            logger.info(
                "vision_call_started",
                role=role,
                requested_model=requested,
                provider=usable[0].name,
                model=usable[0].model,
            )

            answered: dict[str, str] = {}
            send = invoke or default_invoke

            async def _invoke(spec: Any, _request: Any) -> Any:
                request = {
                    "messages": [
                        {
                            "role": "user",
                            "content": vision_content(spec.name.lower(), image, mime_type, prompt),
                        }
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                }
                result = await send(spec, request)
                answered.update(provider=spec.name, model=spec.model)
                return result

            pipeline = ProviderPipeline(
                RoleModelSlots(slots={"vision": usable}), retry_policy=config.retry
            )
            limit = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S
            try:
                with usage_scope(role=role):
                    result = await asyncio.wait_for(
                        pipeline.complete("vision", None, _invoke), limit
                    )
            except TimeoutError as exc:
                span.record_exception(exc)
                raise unavailable(f"no vision model answered within {limit:g} s") from exc
            except AllProvidersFailedError as exc:
                span.record_exception(exc)
                raise unavailable(f"no vision model could answer: {exc}") from exc
            text = result.get("text", "") if isinstance(result, dict) else str(result or "")
            span.set_attribute("vision.provider", answered.get("provider", ""))
            span.set_attribute("vision.model", answered.get("model", ""))
            return VisionReply(
                text=text, provider=answered.get("provider"), model=answered.get("model")
            )

    return call
