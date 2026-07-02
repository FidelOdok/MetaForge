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

import asyncio
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


async def _codex_refresh_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(url, json=body)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result


async def codex_invoke(
    spec: ProviderSpec,
    request: Any,
    *,
    client: Any | None = None,
    credentials: Any | None = None,
) -> dict[str, Any]:
    """Call a model on a ChatGPT subscription via the Codex backend (MET-550).

    Uses the Responses API at the codex backend with the subscription access
    token as bearer + the ``chatgpt-account-id`` header. Credentials come from
    the official Codex CLI login (``~/.codex/auth.json``). ``client`` and
    ``credentials`` are injectable so this unit-tests without network.
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    if client is None:
        from openai import AsyncOpenAI

        from orchestrator.harness.providers import codex_auth

        if credentials is None:
            credentials = await codex_auth.get_valid_credentials(post=_codex_refresh_post)
        client = AsyncOpenAI(
            api_key=credentials.access_token,
            base_url=codex_auth.CODEX_BACKEND_BASE,
            default_headers={
                "chatgpt-account-id": credentials.account_id or "",
                "originator": "codex_cli_rs",
            },
        )
    input_text = "\n\n".join(m.get("content", "") for m in messages)
    try:
        resp = await client.responses.create(
            model=spec.model,
            input=input_text,
            instructions=system or None,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc
    return {"text": getattr(resp, "output_text", "") or "", "model": spec.model}


def _classify_bedrock_error(exc: Exception) -> ProviderError:
    """Map a botocore error to a ProviderError, reading the AWS status/code."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = response.get("Error", {}).get("Code", "")
        if code in {"ThrottlingException", "TooManyRequestsException"}:
            status = 429
        if isinstance(status, int):
            exc.status_code = status  # type: ignore[attr-defined]
    return _classify_error(exc)


async def bedrock_invoke(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> dict[str, Any]:
    """Call an AWS Bedrock model via the Converse API (boto3).

    boto3 is synchronous, so the call runs in a worker thread. Credentials come
    from the standard AWS chain (no api key env). ``client`` is injectable.
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    if client is None:
        import boto3

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        client = boto3.client("bedrock-runtime", region_name=region)
    kwargs: dict[str, Any] = {
        "modelId": spec.model,
        "messages": [
            {"role": m.get("role", "user"), "content": [{"text": m.get("content", "")}]}
            for m in messages
        ],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    try:
        resp = await asyncio.to_thread(client.converse, **kwargs)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify boto errors into ProviderError
        raise _classify_bedrock_error(exc) from exc
    text = resp["output"]["message"]["content"][0]["text"]
    return {"text": text, "model": spec.model}


# Provider-family dispatch by ProviderSpec.name.
_ANTHROPIC_NAMES = frozenset({"anthropic", "claude"})
_GEMINI_NAMES = frozenset({"gemini", "google", "vertex"})
_BEDROCK_NAMES = frozenset({"bedrock", "aws-bedrock"})
_CODEX_NAMES = frozenset({"openai-codex", "codex"})


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
    if name in _BEDROCK_NAMES:
        return await bedrock_invoke(spec, request)
    if name in _CODEX_NAMES:
        return await codex_invoke(spec, request)
    return await openai_invoke(spec, request)
