"""Live provider invoke adapters (MET-548, P0).

Concrete implementations of the pipeline's injected ``Invoke`` seam
(``async (ProviderSpec, request) -> response``) that call real LLM SDKs
(Anthropic, and any OpenAI-compatible endpoint: OpenAI / OpenRouter / vLLM /
Ollama). Errors are classified into :class:`ProviderError` with a status code
so :class:`ProviderPipeline` retries (429/5xx/timeouts) or falls through to the
next provider.

Request schema (dict)::

    {"messages": [{"role": "user"|"assistant", "content": str}],
     "system": str | None, "max_tokens": int, "temperature": float}

or the shorthand ``{"prompt": str}``. Response::

    {"text": str, "model": str}

The SDK client is injectable so the adapters unit-test without network.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from orchestrator.harness.providers.pipeline import ProviderError, ProviderSpec

logger = structlog.get_logger(__name__)

# Exception class names (across SDKs) that mean "transient — retry/failover".
_RETRYABLE_NAMES = frozenset(
    {"RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError"}
)


def _classify_error(exc: Exception) -> ProviderError:
    """Map an SDK exception to a ProviderError with retry semantics."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    retryable = (
        type(exc).__name__ in _RETRYABLE_NAMES
        or status == 429
        or (status is not None and status >= 500)
    )
    return ProviderError(str(exc) or type(exc).__name__, status_code=status, retryable=retryable)


def _normalize_request(request: Any) -> tuple[str | None, list[dict[str, str]], int, float]:
    if not isinstance(request, dict):
        request = {"prompt": str(request)}
    system = request.get("system")
    messages = request.get("messages")
    if not messages:
        messages = [{"role": "user", "content": str(request.get("prompt", ""))}]
    return (
        system,
        list(messages),
        int(request.get("max_tokens", 1024)),
        float(request.get("temperature", 1.0)),
    )


def _require_key(spec: ProviderSpec, default_env: str) -> str:
    env = spec.api_key_env or default_env
    key = os.environ.get(env, "").strip()
    if not key:
        raise ProviderError(f"missing API key in env '{env}'", retryable=False)
    return key


async def anthropic_invoke(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> dict[str, Any]:
    """Call an Anthropic model. ``client`` is injectable for tests."""
    system, messages, max_tokens, temperature = _normalize_request(request)
    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=_require_key(spec, "ANTHROPIC_API_KEY"), base_url=spec.base_url or None
        )
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    try:
        resp = await client.messages.create(**kwargs)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc
    text = "".join(
        getattr(block, "text", "")
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    return {"text": text, "model": getattr(resp, "model", spec.model)}


async def openai_invoke(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> dict[str, Any]:
    """Call an OpenAI-compatible model (OpenAI / OpenRouter / vLLM / Ollama)."""
    system, messages, max_tokens, temperature = _normalize_request(request)
    if system:
        messages = [{"role": "system", "content": system}, *messages]
    if client is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=_require_key(spec, "OPENAI_API_KEY"), base_url=spec.base_url or None
        )
    try:
        resp = await client.chat.completions.create(
            model=spec.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc
    text = resp.choices[0].message.content or ""
    return {"text": text, "model": getattr(resp, "model", spec.model)}


async def gemini_invoke(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> dict[str, Any]:
    """Call a Google Gemini model via the google-genai SDK.

    Messages are flattened into a single ``contents`` string and the system
    prompt is passed as ``system_instruction``. ``client`` is injectable so the
    adapter unit-tests without network.
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    if client is None:
        from google import genai

        client = genai.Client(api_key=_require_key(spec, "GOOGLE_API_KEY"))
    contents = "\n\n".join(m.get("content", "") for m in messages)
    config: dict[str, Any] = {"temperature": temperature, "max_output_tokens": max_tokens}
    if system:
        config["system_instruction"] = system
    try:
        resp = await client.aio.models.generate_content(
            model=spec.model, contents=contents, config=config
        )
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc
    return {"text": getattr(resp, "text", "") or "", "model": spec.model}


# Provider-family dispatch by ProviderSpec.name.
_ANTHROPIC_NAMES = frozenset({"anthropic", "claude"})
_GEMINI_NAMES = frozenset({"gemini", "google", "vertex"})


async def default_invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
    """Dispatch to the right adapter by provider family.

    Anthropic-family names use the Anthropic SDK, Gemini-family names use
    google-genai, and everything else is treated as OpenAI-compatible
    (OpenAI, OpenRouter, vLLM, Ollama, …) via ``base_url``.
    """
    name = spec.name.lower()
    if name in _ANTHROPIC_NAMES:
        return await anthropic_invoke(spec, request)
    if name in _GEMINI_NAMES:
        return await gemini_invoke(spec, request)
    return await openai_invoke(spec, request)
