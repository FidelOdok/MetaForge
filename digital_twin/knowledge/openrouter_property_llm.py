"""OpenRouter-backed ``PropertyLLM`` for L2/L3 property extraction (MET-462).

Production wiring for the Tier-2/3 extractor in
``digital_twin/knowledge/llm_property_extractor.py``. Hits the
OpenRouter chat-completions API with a primary model and an automatic
fallback on 429 / 5xx, mirroring the consolidation pipeline's
``OpenRouterLLMClient`` pattern so operators only have to learn one
provider story.

Env vars (consumed by :meth:`OpenRouterPropertyConfig.from_env`):

* ``OPEN_ROUTER_API_KEY`` — required (shared with the consolidation client).
* ``PROPERTY_EXTRACTION_MODEL`` — primary slug. Default
  ``anthropic/claude-3.5-sonnet``.
* ``PROPERTY_EXTRACTION_FALLBACK_MODEL`` — used on retryable errors.
  Default ``meta-llama/llama-3-70b-instruct``.
* ``PROPERTY_EXTRACTION_TEMPERATURE`` — float, default ``0.0``. Extraction
  is deterministic; temperature stays low.
* ``PROPERTY_EXTRACTION_MAX_TOKENS`` — int, default ``800``. Single-
  property JSON payloads are small.

The adapter implements :class:`~digital_twin.knowledge.llm_property_extractor.PropertyLLM`
``complete(prompt) -> str``: it returns the raw model output so the
extractor's own JSON-fence parser can run, and clients keep one place
to update prompt or schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.openrouter_property_llm")

DEFAULT_PRIMARY_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_FALLBACK_MODEL = "meta-llama/llama-3-70b-instruct"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 800
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_HTTP_REFERER = "https://github.com/FidelOdok/MetaForge"
DEFAULT_X_TITLE = "MetaForge property extraction"

_SYSTEM_PROMPT = (
    "You extract a single typed property from an electronics datasheet. "
    "Reply with ONLY a JSON object that matches the schema in the prompt. "
    "No prose, no markdown fences, no commentary."
)
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


class OpenRouterPropertyError(RuntimeError):
    """Raised when both the primary and the fallback OpenRouter call fail."""


@dataclass(frozen=True)
class OpenRouterPropertyConfig:
    """Tunables for :class:`OpenRouterPropertyLLM`."""

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
    def from_env(cls) -> OpenRouterPropertyConfig:
        api_key = os.environ.get("OPEN_ROUTER_API_KEY", "")
        if not api_key:
            raise OpenRouterPropertyError(
                "OPEN_ROUTER_API_KEY is not set; cannot build OpenRouterPropertyLLM"
            )
        return cls(
            api_key=api_key,
            primary_model=os.environ.get("PROPERTY_EXTRACTION_MODEL", DEFAULT_PRIMARY_MODEL),
            fallback_model=os.environ.get(
                "PROPERTY_EXTRACTION_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL
            ),
            temperature=_env_float("PROPERTY_EXTRACTION_TEMPERATURE", DEFAULT_TEMPERATURE),
            max_tokens=_env_int("PROPERTY_EXTRACTION_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        )


class OpenRouterPropertyLLM:
    """``PropertyLLM`` adapter calling the OpenRouter chat-completions API.

    Construction is cheap; the underlying ``httpx.AsyncClient`` is built
    lazily on the first ``complete`` call. Callers that share an event
    loop should ``await close()`` on shutdown to release the HTTP pool.
    """

    def __init__(
        self,
        config: OpenRouterPropertyConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def complete(self, prompt: str) -> str:
        """Run ``prompt`` against the primary model; fall back on retryable errors.

        Returns the model's raw text response. The L2/L3 extractor parses
        the JSON itself so this stays focused on transport.
        """
        with tracer.start_as_current_span("knowledge.openrouter_property.complete") as span:
            span.set_attribute("knowledge.primary_model", self._config.primary_model)
            span.set_attribute("knowledge.prompt_length", len(prompt))
            try:
                return await self._call(self._config.primary_model, prompt)
            except _RetryableError as exc:
                span.set_attribute("knowledge.fallback_invoked", True)
                logger.warning(
                    "openrouter_property_primary_fallback",
                    primary=self._config.primary_model,
                    fallback=self._config.fallback_model,
                    status=exc.status_code,
                )
                try:
                    return await self._call(self._config.fallback_model, prompt)
                except Exception as fallback_exc:
                    span.record_exception(fallback_exc)
                    raise OpenRouterPropertyError(
                        f"both primary ({self._config.primary_model}) and fallback "
                        f"({self._config.fallback_model}) failed: {fallback_exc}"
                    ) from fallback_exc
            except OpenRouterPropertyError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise OpenRouterPropertyError(f"OpenRouter call failed: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _call(self, model: str, prompt: str) -> str:
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
        return _extract_message_content(response.json())

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout_seconds,
            )
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        # OpenRouter's "X-Title" / "HTTP-Referer" are optional but help
        # operators see usage attribution on the dashboard.
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._config.http_referer,
            "X-Title": self._config.x_title,
        }


class _RetryableError(Exception):
    """Internal: primary returned a status worth retrying on the fallback model."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message[:200]}")
        self.status_code = status_code


def _extract_message_content(body: dict[str, Any]) -> str:
    """Pull ``choices[0].message.content`` defensively.

    Some OpenRouter upstreams trim fields when a request limit fires —
    raise a clear error instead of crashing three levels deep.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterPropertyError(f"no choices in response: {body!r}")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise OpenRouterPropertyError(f"missing message in first choice: {choices[0]!r}")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterPropertyError(f"empty content in message: {message!r}")
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
