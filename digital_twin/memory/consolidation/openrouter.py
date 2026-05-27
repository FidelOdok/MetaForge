"""Open Router-backed ``LLMClient`` for the consolidation pipeline.

Hits the Open Router chat-completions API with a primary model and an
automatic fallback model on 429 / 5xx. Configuration is env-driven so
deployments can lock to cost-efficient models (Llama 3) in staging /
dev and Claude in production without code changes.

Env vars (all consumed by ``OpenRouterLLMClient.from_env``):

* ``OPEN_ROUTER_API_KEY`` — required
* ``CONSOLIDATION_MODEL`` — primary model slug. Defaults to
  ``anthropic/claude-3.5-sonnet``.
* ``CONSOLIDATION_FALLBACK_MODEL`` — used when the primary returns a
  retryable error. Defaults to ``meta-llama/llama-3-70b-instruct``.
* ``CONSOLIDATION_TEMPERATURE`` — float, default 0.7.
* ``CONSOLIDATION_MAX_TOKENS`` — int, default 2000.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from digital_twin.memory.consolidation.llm import LLMClient, parse_strict_json
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.openrouter")

DEFAULT_PRIMARY_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_FALLBACK_MODEL = "meta-llama/llama-3-70b-instruct"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 2000
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0

_SYSTEM_PROMPT = (
    "You synthesize design insights from agent task outcomes. Always reply "
    "with strict JSON. No prose, no markdown fences."
)
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


class OpenRouterError(RuntimeError):
    """Raised when both primary and fallback models fail."""


@dataclass(frozen=True)
class OpenRouterConfig:
    """Tunables for ``OpenRouterLLMClient``.

    ``from_env`` reads each value from the matching env var, falling back
    to the module-level defaults so missing config never crashes the
    consolidation pipeline at construction time.
    """

    api_key: str
    primary_model: str = DEFAULT_PRIMARY_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> OpenRouterConfig:
        api_key = os.environ.get("OPEN_ROUTER_API_KEY", "")
        if not api_key:
            raise OpenRouterError(
                "OPEN_ROUTER_API_KEY is not set; cannot build OpenRouterLLMClient"
            )
        return cls(
            api_key=api_key,
            primary_model=os.environ.get("CONSOLIDATION_MODEL", DEFAULT_PRIMARY_MODEL),
            fallback_model=os.environ.get("CONSOLIDATION_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL),
            temperature=_env_float("CONSOLIDATION_TEMPERATURE", DEFAULT_TEMPERATURE),
            max_tokens=_env_int("CONSOLIDATION_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        )


class OpenRouterLLMClient(LLMClient):
    """``LLMClient`` adapter calling the Open Router chat-completions API.

    Construction is cheap; the underlying ``httpx.AsyncClient`` is
    created lazily on first call. Callers that share an event loop
    across many synthesizer calls should ``await close()`` on shutdown.
    """

    def __init__(
        self,
        config: OpenRouterConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def synthesize_insight(self, prompt: str) -> dict[str, Any]:
        with tracer.start_as_current_span("consolidation.openrouter.synthesize") as span:
            span.set_attribute("memory.primary_model", self._config.primary_model)
            try:
                return await self._call(self._config.primary_model, prompt)
            except _RetryableError as exc:
                span.set_attribute("memory.fallback_invoked", True)
                logger.warning(
                    "openrouter_primary_fallback",
                    primary=self._config.primary_model,
                    fallback=self._config.fallback_model,
                    status=exc.status_code,
                )
                try:
                    return await self._call(self._config.fallback_model, prompt)
                except Exception as fallback_exc:
                    span.record_exception(fallback_exc)
                    raise OpenRouterError(
                        f"both primary ({self._config.primary_model}) and fallback "
                        f"({self._config.fallback_model}) failed: {fallback_exc}"
                    ) from fallback_exc
            except OpenRouterError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise OpenRouterError(f"open router call failed: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _call(self, model: str, prompt: str) -> dict[str, Any]:
        client = await self._get_client()
        payload = {
            "model": model,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        response = await client.post(
            "/chat/completions",
            json=payload,
            headers=self._auth_headers(),
        )
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise _RetryableError(response.status_code, response.text)
        response.raise_for_status()
        body = response.json()
        text = _extract_message_content(body)
        return parse_strict_json(text)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout_seconds,
            )
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }


class _RetryableError(Exception):
    """Internal flag — primary returned a status worth retrying on the fallback."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message[:200]}")
        self.status_code = status_code


def _extract_message_content(body: dict[str, Any]) -> str:
    """Pull ``choices[0].message.content`` defensively.

    Open Router mirrors the OpenAI chat-completions shape, but the
    handful of providers behind it occasionally trim fields. Raise a
    clear error rather than crashing on a missing key three levels deep.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterError(f"no choices in response: {body!r}")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise OpenRouterError(f"missing message in first choice: {choices[0]!r}")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError(f"empty content in message: {message!r}")
    return content


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("openrouter_env_float_invalid", name=name, value=raw)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("openrouter_env_int_invalid", name=name, value=raw)
        return default
