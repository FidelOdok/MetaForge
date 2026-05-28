"""OpenRouter-backed LightRAG ``llm_model_func`` (MET-466 Task 2).

LightRAG's constructor demands an ``llm_model_func`` with the signature
``(prompt, system_prompt=None, history_messages=None, **kwargs) -> str``.
The default in ``lightrag_service`` is :func:`_noop_llm_model_func`,
which keeps KG / entity extraction effectively disabled so naive vector
mode is the operational default.

This module supplies a production-grade factory:
:func:`build_openrouter_llm_model_func` returns a callable matching
LightRAG's signature and routes every call through the OpenRouter
chat-completions API with a configurable primary + fallback model,
shared ``OPEN_ROUTER_API_KEY`` env var (consistent with the consolidation
client and ``OpenRouterPropertyLLM``).

Wire-up pattern in :class:`LightRAGConfig` is opt-in: leave
``llm_model_func`` as ``None`` and naive vector mode keeps working
unchanged; supply the OpenRouter factory's return value to enable LightRAG
entity-extraction once Neo4j is live.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.openrouter_lightrag")

# Defaults pinned for KG entity extraction. Claude 3.5 Sonnet is the
# primary in line with the rest of the platform; Llama 3 70B is the
# fallback on transient errors.
DEFAULT_PRIMARY_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_FALLBACK_MODEL = "meta-llama/llama-3-70b-instruct"
# Slight temperature room — entity extraction benefits from a little
# flexibility in span identification, unlike single-property answers
# which are pinned at 0.0.
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2000
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_HTTP_REFERER = "https://github.com/FidelOdok/MetaForge"
DEFAULT_X_TITLE = "MetaForge LightRAG"

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

# Type alias matching LightRAG's expected signature.
LightRAGModelFunc = Callable[..., Awaitable[str]]


class OpenRouterLightRAGError(RuntimeError):
    """Raised when both the primary and the fallback OpenRouter call fail."""


@dataclass(frozen=True)
class OpenRouterLightRAGConfig:
    """Tunables for the LightRAG OpenRouter llm_model_func."""

    api_key: str
    primary_model: str = DEFAULT_PRIMARY_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    http_referer: str = DEFAULT_HTTP_REFERER
    x_title: str = DEFAULT_X_TITLE

    @classmethod
    def from_env(cls) -> OpenRouterLightRAGConfig:
        api_key = os.environ.get("OPEN_ROUTER_API_KEY", "")
        if not api_key:
            raise OpenRouterLightRAGError(
                "OPEN_ROUTER_API_KEY is not set; cannot build the LightRAG llm_model_func"
            )
        return cls(
            api_key=api_key,
            primary_model=os.environ.get("LIGHTRAG_MODEL", DEFAULT_PRIMARY_MODEL),
            fallback_model=os.environ.get("LIGHTRAG_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL),
            temperature=_env_float("LIGHTRAG_TEMPERATURE", DEFAULT_TEMPERATURE),
            max_tokens=_env_int("LIGHTRAG_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        )


def build_openrouter_llm_model_func(
    config: OpenRouterLightRAGConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> LightRAGModelFunc:
    """Return an async callable with LightRAG's ``llm_model_func`` signature.

    The returned function accepts LightRAG's keyword arguments
    (``system_prompt``, ``history_messages``, plus ``**kwargs`` for any
    forward-compat fields the framework adds) and posts a chat request
    to OpenRouter, falling back to the secondary model on 408/429/5xx.
    ``client`` is for tests — production passes nothing and the function
    builds its own pooled :class:`httpx.AsyncClient` on first use.
    """
    state: dict[str, Any] = {"client": client, "owns_client": client is None}

    async def _get_client() -> httpx.AsyncClient:
        if state["client"] is None:
            state["client"] = httpx.AsyncClient(
                base_url=config.base_url, timeout=config.timeout_seconds
            )
            state["owns_client"] = True
        return state["client"]  # type: ignore[no-any-return]

    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": config.http_referer,
            "X-Title": config.x_title,
        }

    def _messages(
        prompt: str,
        system_prompt: str | None,
        history_messages: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for hist in history_messages or []:
            role = str(hist.get("role", "user"))
            content = str(hist.get("content", ""))
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _call(model: str, messages: list[dict[str, str]]) -> str:
        client_obj = await _get_client()
        response = await client_obj.post(
            "/chat/completions",
            json={
                "model": model,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "messages": messages,
            },
            headers=_headers(),
        )
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise _RetryableError(response.status_code, response.text)
        response.raise_for_status()
        return _extract_message_content(response.json())

    async def llm_model_func(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        **_kwargs: Any,
    ) -> str:
        with tracer.start_as_current_span("knowledge.openrouter_lightrag.complete") as span:
            span.set_attribute("knowledge.primary_model", config.primary_model)
            span.set_attribute("knowledge.prompt_length", len(prompt))
            messages = _messages(prompt, system_prompt, history_messages)
            try:
                return await _call(config.primary_model, messages)
            except _RetryableError as exc:
                span.set_attribute("knowledge.fallback_invoked", True)
                logger.warning(
                    "openrouter_lightrag_primary_fallback",
                    primary=config.primary_model,
                    fallback=config.fallback_model,
                    status=exc.status_code,
                )
                try:
                    return await _call(config.fallback_model, messages)
                except Exception as fallback_exc:
                    span.record_exception(fallback_exc)
                    raise OpenRouterLightRAGError(
                        f"both primary ({config.primary_model}) and fallback "
                        f"({config.fallback_model}) failed: {fallback_exc}"
                    ) from fallback_exc
            except OpenRouterLightRAGError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise OpenRouterLightRAGError(f"OpenRouter LightRAG call failed: {exc}") from exc

    async def close() -> None:
        if state["client"] is not None and state["owns_client"]:
            await state["client"].aclose()
            state["client"] = None

    # Attach close so callers (and the service's shutdown path) can release
    # the httpx pool without holding a reference to the closure's state.
    llm_model_func.close = close  # type: ignore[attr-defined]
    return llm_model_func


class _RetryableError(Exception):
    """Internal: status worth retrying on the fallback model."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message[:200]}")
        self.status_code = status_code


def _extract_message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterLightRAGError(f"no choices in response: {body!r}")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise OpenRouterLightRAGError(f"missing message in first choice: {choices[0]!r}")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterLightRAGError(f"empty content in message: {message!r}")
    return content


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default
