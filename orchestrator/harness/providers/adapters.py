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
import json
import os
from collections.abc import AsyncIterator
from typing import Any, cast

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


DEFAULT_MAX_OUTPUT_TOKENS = 8192


def default_max_output_tokens() -> int:
    """Output-token cap applied when a request doesn't set ``max_tokens``.

    ``METAFORGE_MAX_OUTPUT_TOKENS`` overrides (for models with smaller or
    larger output limits). The old default of 1024 silently truncated long
    answers and large tool arguments on every completion (MET-565).
    """
    raw = (os.environ.get("METAFORGE_MAX_OUTPUT_TOKENS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_MAX_OUTPUT_TOKENS


def _usage_from(
    obj: Any, in_key: str = "input_tokens", out_key: str = "output_tokens"
) -> dict[str, int] | None:
    """Best-effort {input_tokens, output_tokens} from a provider usage object (MET-596)."""
    u = getattr(obj, "usage", None)
    if u is None:
        return None
    try:
        return {
            "input_tokens": int(getattr(u, in_key, 0) or 0),
            "output_tokens": int(getattr(u, out_key, 0) or 0),
        }
    except (TypeError, ValueError):
        return None


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
        int(request.get("max_tokens", default_max_output_tokens())),
        float(request.get("temperature", 1.0)),
    )


def _require_key(spec: ProviderSpec, default_env: str) -> str:
    # A raw key from the gateway auth store (`forge auth login`) wins over the
    # environment; fall back to the env var named by the spec/profile.
    if spec.api_key and spec.api_key.strip():
        return spec.api_key.strip()
    env = spec.api_key_env or default_env
    key = os.environ.get(env, "").strip()
    if not key:
        raise ProviderError(f"missing API key in env '{env}'", retryable=False)
    return key


def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI function schemas to Anthropic tool schemas.

    OpenAI: ``{"type":"function","function":{"name","description","parameters"}}``
    Anthropic: ``{"name","description","input_schema"}``.
    """
    out: list[dict[str, Any]] = []
    for t in tools:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object"},
            }
        )
    return out


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI-canonical messages to Anthropic content-block form.

    The native loop speaks OpenAI shape (assistant ``tool_calls`` + ``tool`` role
    results). Anthropic instead wants ``tool_use`` blocks on the assistant turn
    and ``tool_result`` blocks grouped into a single following user turn — so we
    coalesce consecutive ``tool`` messages into one user message.
    """
    out: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id"),
                    "content": str(m.get("content", "")),
                }
            )
            continue
        flush()
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc["function"]
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    inp = {}
                blocks.append(
                    {"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": inp}
                )
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": role, "content": m.get("content", "")})
    flush()
    return out


async def anthropic_invoke(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> dict[str, Any]:
    """Call an Anthropic model. ``client`` is injectable for tests.

    When the request carries ``tools`` (native tool-calling), the OpenAI-shaped
    tools/messages are translated to Anthropic's block form and any ``tool_use``
    blocks in the reply are returned as ``tool_calls`` — so the native loop drives
    Claude models the same way it drives OpenAI-compatible ones.
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    tools = request.get("tools") if isinstance(request, dict) else None
    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=_require_key(spec, "ANTHROPIC_API_KEY"), base_url=spec.base_url or None
        )
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": _to_anthropic_messages(messages) if tools else messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = _to_anthropic_tools(tools)
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
    tool_calls = [
        {
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "arguments": dict(getattr(block, "input", None) or {}),
        }
        for block in resp.content
        if getattr(block, "type", None) == "tool_use"
    ]
    result: dict[str, Any] = {"text": text, "model": getattr(resp, "model", spec.model)}
    if tool_calls:
        result["tool_calls"] = tool_calls
    usage = _usage_from(resp)
    if usage:
        result["usage"] = usage
    return result


async def openai_invoke(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> dict[str, Any]:
    """Call an OpenAI-compatible model (OpenAI / OpenRouter / vLLM / Ollama).

    When the request carries ``tools`` (native function schemas), they are passed
    through and any ``tool_calls`` in the reply are parsed and returned — this is
    the native tool-calling path (matches how Claude Code drives tools).
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    if system:
        messages = [{"role": "system", "content": system}, *messages]
    if client is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=_require_key(spec, "OPENAI_API_KEY"), base_url=spec.base_url or None
        )
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    tools = request.get("tools") if isinstance(request, dict) else None
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = request.get("tool_choice", "auto")
    try:
        resp = await client.chat.completions.create(**kwargs)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc
    msg = resp.choices[0].message
    tool_calls: list[dict[str, Any]] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except (ValueError, TypeError):
            args = {}
        tool_calls.append(
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args if isinstance(args, dict) else {},
            }
        )
    out: dict[str, Any] = {
        "text": msg.content or "",
        "tool_calls": tool_calls,
        "model": getattr(resp, "model", spec.model),
    }
    usage = _usage_from(resp, "prompt_tokens", "completion_tokens")
    if usage:
        out["usage"] = usage
    return out


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

        # cast(Any, …): keep the SDK seam untyped — request payloads here are
        # normalized plain dicts, which the SDK accepts at runtime but whose
        # narrowed client type makes mypy demand its TypedDict param shapes.
        client = cast(Any, genai.Client(api_key=_require_key(spec, "GOOGLE_API_KEY")))
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
        # OpenAI's token endpoint expects form-encoded, not JSON.
        resp = await http.post(url, data=body)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result


# Default system prompt — the Codex Responses API requires non-empty instructions.
_CODEX_DEFAULT_INSTRUCTIONS = "You are a helpful assistant."


def _codex_client(credentials: Any) -> Any:
    from openai import AsyncOpenAI

    from orchestrator.harness.providers import codex_auth

    return AsyncOpenAI(
        api_key=credentials.access_token,
        base_url=codex_auth.CODEX_BACKEND_BASE,
        default_headers={
            "chatgpt-account-id": credentials.account_id or "",
            "originator": "codex_cli_rs",
        },
    )


async def _codex_stream_deltas(
    client: Any, spec: ProviderSpec, system: str | None, input_text: str
) -> AsyncIterator[str]:
    """Open the codex Responses stream and yield ``output_text.delta`` chunks.

    The codex backend's Responses API requires ``input`` as a list of typed
    items (a bare string is rejected), ``store=False``, and ``stream=True`` (it
    is streaming-only). Shared by the aggregating :func:`_codex_call` and the
    streaming :func:`codex_stream` so the request shape lives in one place.
    """
    stream = await client.responses.create(
        model=spec.model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": input_text}]}],
        instructions=system or _CODEX_DEFAULT_INSTRUCTIONS,  # required, non-empty
        store=False,
        stream=True,
    )
    async for event in stream:
        etype = getattr(event, "type", "")
        if etype == "response.output_text.delta":
            yield getattr(event, "delta", "") or ""
        elif etype in ("response.incomplete", "response.failed"):
            # MET-614: a length-capped or failed response used to return
            # silently truncated text, which downstream parses as a malformed
            # ReAct reply and burns a recovery round-trip. Surface it as
            # retryable — a fresh generation usually fits.
            response = getattr(event, "response", None)
            details = getattr(response, "incomplete_details", None) or getattr(
                response, "error", None
            )
            reason = getattr(details, "reason", None) or getattr(details, "message", None)
            raise ProviderError(
                f"codex response ended '{etype.removeprefix('response.')}'"
                + (f" ({reason})" if reason else ""),
                retryable=True,
            )


async def _codex_call(
    client: Any, spec: ProviderSpec, system: str | None, input_text: str
) -> dict[str, Any]:
    parts = [delta async for delta in _codex_stream_deltas(client, spec, system, input_text)]
    return {"text": "".join(parts), "model": spec.model}


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
    the official Codex CLI login (``~/.codex/auth.json``); on a 401 the token is
    refreshed and the call retried once (the codex CLI does the same — a stored
    token can be invalidated even before its ``exp``). ``client`` and
    ``credentials`` are injectable so this unit-tests without network.
    """
    system, messages, _max_tokens, _temperature = _normalize_request(request)
    input_text = "\n\n".join(m.get("content", "") for m in messages)

    # MET-575: this adapter cannot forward native tool schemas (the codex
    # Responses call is built from flattened text). Silently dropping them
    # produced turns where the model claimed "no tools available" while the
    # harness had dozens registered. The path decision now routes codex to
    # ReAct (tools travel as text), so reaching here with tools means a
    # caller bypassed that — make the drop loud instead of silent.
    if isinstance(request, dict) and request.get("tools"):
        logger.warning(
            "codex_native_tools_dropped",
            n_tools=len(request["tools"]),
            model=spec.model,
        )

    # Injected client (tests) → single call, no auth handling.
    if client is not None:
        try:
            return await _codex_call(client, spec, system, input_text)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify_error(exc) from exc

    from orchestrator.harness.providers import codex_auth

    path = codex_auth.auth_json_path()
    creds = credentials or await codex_auth.get_valid_credentials(
        path=path, post=_codex_refresh_post
    )
    try:
        return await _codex_call(_codex_client(creds), spec, system, input_text)
    except Exception as exc:  # noqa: BLE001
        err = exc if isinstance(exc, ProviderError) else _classify_error(exc)
        # Refresh-and-retry once on auth failure (stored token invalidated).
        # Persist the rotated tokens so the next process doesn't reuse a dead one.
        if err.status_code == 401 and getattr(creds, "refresh_token", None):
            fresh = await codex_auth.refresh_credentials(creds, post=_codex_refresh_post)
            if path is not None:
                codex_auth.save_credentials(path, fresh)
            return await _codex_call(_codex_client(fresh), spec, system, input_text)
        raise err from exc


async def codex_stream(
    spec: ProviderSpec,
    request: Any,
    *,
    client: Any | None = None,
    credentials: Any | None = None,
) -> AsyncIterator[str]:
    """Stream a ChatGPT-subscription response via the codex Responses backend.

    Shares the request shape + auth resolution with :func:`codex_invoke`.
    Credentials are refreshed proactively when expired; unlike ``codex_invoke``
    there is no mid-stream 401 refresh-retry (a rare invalidated-token stream
    surfaces as an error the caller can fall back on).
    """
    system, messages, _max_tokens, _temperature = _normalize_request(request)
    input_text = "\n\n".join(m.get("content", "") for m in messages)

    if client is not None:
        async for delta in _codex_stream_deltas(client, spec, system, input_text):
            yield delta
        return

    from orchestrator.harness.providers import codex_auth

    creds = credentials or await codex_auth.get_valid_credentials(
        path=codex_auth.auth_json_path(), post=_codex_refresh_post
    )
    async for delta in _codex_stream_deltas(_codex_client(creds), spec, system, input_text):
        yield delta


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


# ---------------------------------------------------------------------------
# Streaming adapters (MET-548) — yield text deltas for the final chat answer.
# ---------------------------------------------------------------------------


async def anthropic_stream(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> AsyncIterator[str]:
    """Stream an Anthropic model's text deltas."""
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
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc


async def openai_stream(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> AsyncIterator[str]:
    """Stream an OpenAI-compatible model's text deltas."""
    system, messages, max_tokens, temperature = _normalize_request(request)
    if system:
        messages = [{"role": "system", "content": system}, *messages]
    if client is None:
        from openai import AsyncOpenAI

        # cast(Any, …): keep the SDK seam untyped — messages are normalized
        # plain dicts and ``stream=True`` returns an async iterator; the
        # narrowed client type makes mypy demand SDK TypedDicts and a union
        # return that hides ``__aiter__``.
        client = cast(
            Any,
            AsyncOpenAI(
                api_key=_require_key(spec, "OPENAI_API_KEY"), base_url=spec.base_url or None
            ),
        )
    try:
        stream = await client.chat.completions.create(
            model=spec.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc


async def gemini_stream(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> AsyncIterator[str]:
    """Stream a Google Gemini model's text deltas."""
    system, messages, max_tokens, temperature = _normalize_request(request)
    if client is None:
        from google import genai

        # cast(Any, …): keep the SDK seam untyped — request payloads here are
        # normalized plain dicts, which the SDK accepts at runtime but whose
        # narrowed client type makes mypy demand its TypedDict param shapes.
        client = cast(Any, genai.Client(api_key=_require_key(spec, "GOOGLE_API_KEY")))
    contents = "\n\n".join(m.get("content", "") for m in messages)
    config: dict[str, Any] = {"temperature": temperature, "max_output_tokens": max_tokens}
    if system:
        config["system_instruction"] = system
    try:
        stream = await client.aio.models.generate_content_stream(
            model=spec.model, contents=contents, config=config
        )
        async for chunk in stream:
            text = getattr(chunk, "text", "") or ""
            if text:
                yield text
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc


async def bedrock_stream(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> AsyncIterator[str]:
    """Bedrock streaming is *buffered*: the Converse result is yielded as a
    single delta. ``converse_stream``'s sync EventStream doesn't bridge cleanly
    to an async generator, and Bedrock is rarely the interactive chat provider.
    """
    result = await bedrock_invoke(spec, request, client=client)
    text = result.get("text", "")
    if text:
        yield text


def default_stream(spec: ProviderSpec, request: Any) -> AsyncIterator[str]:
    """Dispatch to the right streaming adapter by provider family (see
    :func:`default_invoke`)."""
    name = spec.name.lower()
    if name in _ANTHROPIC_NAMES:
        return anthropic_stream(spec, request)
    if name in _GEMINI_NAMES:
        return gemini_stream(spec, request)
    if name in _BEDROCK_NAMES:
        return bedrock_stream(spec, request)
    if name in _CODEX_NAMES:
        return codex_stream(spec, request)
    return openai_stream(spec, request)


# ---------------------------------------------------------------------------
# Event streaming (MET-591): text deltas + assembled tool calls in one stream
# ---------------------------------------------------------------------------
#
# The plain ``*_stream`` adapters above yield only text and drop ``tools`` —
# unusable inside the native tool-calling loop. These variants stream the SAME
# request shape ``*_invoke`` takes and yield structured events:
#
#     {"type": "text_delta", "text": str}          # as tokens generate
#     {"type": "response", "result": {...}}        # terminal; result matches
#                                                  # the *_invoke return shape
#
# so the loop can forward live text while still receiving the exact response
# object it would have gotten from the non-streaming call.


class StreamingUnsupported(ProviderError):
    """This provider family has no event-streaming adapter — fall back to
    the non-streaming invoke (never retried; not a provider fault)."""

    def __init__(self, family: str) -> None:
        super().__init__(
            f"event streaming unsupported for provider family '{family}'", retryable=False
        )


async def openai_stream_events(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Stream an OpenAI-compatible completion as events (text + tool calls).

    Tool-call fragments arrive per chunk keyed by ``index`` (``id``/``name``
    once, ``function.arguments`` split across chunks) and are assembled into
    the same ``tool_calls`` shape :func:`openai_invoke` returns.
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    if system:
        messages = [{"role": "system", "content": system}, *messages]
    if client is None:
        from openai import AsyncOpenAI

        client = cast(
            Any,
            AsyncOpenAI(
                api_key=_require_key(spec, "OPENAI_API_KEY"), base_url=spec.base_url or None
            ),
        )
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        # MET-596: ask for usage on the final stream chunk (OpenAI-compatible
        # servers that don't know the option simply ignore it).
        "stream_options": {"include_usage": True},
    }
    tools = request.get("tools") if isinstance(request, dict) else None
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = request.get("tool_choice", "auto")

    text_parts: list[str] = []
    acc: dict[int, dict[str, str]] = {}
    announced: set[int] = set()
    usage: dict[str, int] | None = None
    try:
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            usage = _usage_from(chunk, "prompt_tokens", "completion_tokens") or usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                yield {"type": "text_delta", "text": content}
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = int(getattr(tc, "index", 0) or 0)
                slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] += fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments
                # MET-592 "typed from step zero": announce the action the
                # moment its NAME is known — long before arguments finish.
                if slot["name"] and idx not in announced:
                    announced.add(idx)
                    yield {"type": "action_started", "name": slot["name"]}
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc

    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(acc):
        slot = acc[idx]
        try:
            args = json.loads(slot["arguments"] or "{}")
        except (ValueError, TypeError):
            args = {}
        tool_calls.append(
            {
                "id": slot["id"],
                "name": slot["name"],
                "arguments": args if isinstance(args, dict) else {},
            }
        )
    result: dict[str, Any] = {"text": "".join(text_parts), "model": spec.model}
    if tool_calls:
        result["tool_calls"] = tool_calls
    if usage:
        result["usage"] = usage
    yield {"type": "response", "result": result}


async def anthropic_stream_events(
    spec: ProviderSpec, request: Any, *, client: Any | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Stream an Anthropic completion as events (text + tool calls).

    Text arrives as ``text_delta`` events; ``tool_use`` blocks assemble from
    ``content_block_start`` + ``input_json_delta``. The terminal response is
    taken from the SDK's accumulated final message and parsed exactly like
    :func:`anthropic_invoke`.
    """
    system, messages, max_tokens, temperature = _normalize_request(request)
    tools = request.get("tools") if isinstance(request, dict) else None
    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=_require_key(spec, "ANTHROPIC_API_KEY"), base_url=spec.base_url or None
        )
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": _to_anthropic_messages(messages) if tools else messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = _to_anthropic_tools(tools)

    try:
        async with client.messages.stream(**kwargs) as stream:
            # Raw event iteration (MET-592): typed blocks instead of the
            # text-only convenience stream — thinking_delta (extended
            # thinking) is distinguishable from text_delta, and a tool_use
            # block announces its NAME at content_block_start, before its
            # input_json_delta arguments finish assembling.
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        yield {"type": "action_started", "name": getattr(block, "name", "")}
                elif etype == "content_block_delta":
                    d = getattr(event, "delta", None)
                    dtype = getattr(d, "type", "")
                    text = getattr(d, "text", None)
                    thinking = getattr(d, "thinking", None)
                    if dtype == "text_delta" and text:
                        yield {"type": "text_delta", "text": text}
                    elif dtype == "thinking_delta" and thinking:
                        yield {"type": "thinking_delta", "text": thinking}
            final = await stream.get_final_message()
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify SDK errors into ProviderError
        raise _classify_error(exc) from exc

    text = "".join(
        getattr(block, "text", "")
        for block in final.content
        if getattr(block, "type", None) == "text"
    )
    tool_calls = [
        {
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "arguments": dict(getattr(block, "input", None) or {}),
        }
        for block in final.content
        if getattr(block, "type", None) == "tool_use"
    ]
    result: dict[str, Any] = {"text": text, "model": getattr(final, "model", spec.model)}
    if tool_calls:
        result["tool_calls"] = tool_calls
    usage = _usage_from(final)
    if usage:
        result["usage"] = usage
    yield {"type": "response", "result": result}


def default_stream_events(spec: ProviderSpec, request: Any) -> AsyncIterator[dict[str, Any]]:
    """Dispatch to the event-streaming adapter by provider family.

    Families without one (gemini / bedrock / codex — codex's Responses stream
    cannot carry the loop's tool schemas) raise :class:`StreamingUnsupported`,
    which the pipeline treats as non-retryable so callers fall back to the
    non-streaming invoke with identical behavior.
    """
    name = spec.name.lower()
    if name in _ANTHROPIC_NAMES:
        return anthropic_stream_events(spec, request)
    if name in _GEMINI_NAMES or name in _BEDROCK_NAMES or name in _CODEX_NAMES:

        async def _unsupported() -> AsyncIterator[dict[str, Any]]:
            raise StreamingUnsupported(name)
            yield {}  # pragma: no cover - makes this an async generator

        return _unsupported()
    return openai_stream_events(spec, request)
