"""Base agent module for PydanticAI-powered domain agents.

Provides common infrastructure for wrapping PydanticAI Agent() with
graceful degradation to hardcoded dispatch when no LLM is available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.base")


# ---------------------------------------------------------------------------
# Dependencies dataclass — injected into PydanticAI RunContext
# ---------------------------------------------------------------------------


@dataclass
class AgentDependencies:
    """Dependencies injected into PydanticAI agent tool calls via RunContext."""

    twin: Any  # TwinAPI — avoid circular import
    mcp_bridge: McpBridge
    session_id: str
    branch: str = "main"


# ---------------------------------------------------------------------------
# Common result model
# ---------------------------------------------------------------------------


class AgentResult(BaseModel):
    """Structured output from a PydanticAI agent run."""

    work_products: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Work products produced or modified by the agent",
    )
    analysis: dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis report from the agent",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations from the agent",
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Record of tool calls made during execution",
    )


# ---------------------------------------------------------------------------
# LLM configuration helpers
# ---------------------------------------------------------------------------


def get_llm_model() -> Any | None:
    """Resolve the LLM model from environment variables.

    Supported env vars:
        METAFORGE_LLM_PROVIDER: 'openai' or 'anthropic'
        METAFORGE_LLM_MODEL: model name (e.g. 'claude-sonnet-4-20250514')
        METAFORGE_LLM_BASE_URL: custom API base URL for proxy setups
            (e.g. CLIProxyAPI, LiteLLM)
        METAFORGE_LLM_API_KEY: API key for the provider or proxy

    Returns a PydanticAI-compatible model string or model instance,
    or None if the LLM provider is not configured.
    """
    provider = os.environ.get("METAFORGE_LLM_PROVIDER", "").strip().lower()
    if not provider:
        return None

    model_name = os.environ.get("METAFORGE_LLM_MODEL", "").strip()
    base_url = os.environ.get("METAFORGE_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("METAFORGE_LLM_API_KEY", "").strip()

    if provider == "openai":
        if base_url:
            try:
                from pydantic_ai.models.openai import OpenAIModel
                from pydantic_ai.providers.openai import OpenAIProvider

                return OpenAIModel(
                    model_name or "gpt-4o",
                    provider=OpenAIProvider(
                        api_key=api_key or None,
                        base_url=base_url,
                    ),
                )
            except ImportError:
                logger.warning("openai_model_import_failed")
                return None
        return f"openai:{model_name or 'gpt-4o'}"
    elif provider == "anthropic":
        if base_url:
            try:
                from pydantic_ai.models.anthropic import AnthropicModel
                from pydantic_ai.providers.anthropic import AnthropicProvider

                return AnthropicModel(
                    model_name or "claude-sonnet-4-20250514",
                    provider=AnthropicProvider(
                        api_key=api_key or None,
                        base_url=base_url,
                    ),
                )
            except ImportError:
                logger.warning("anthropic_model_import_failed")
                return None
        return f"anthropic:{model_name or 'claude-sonnet-4-20250514'}"
    else:
        logger.warning("unknown_llm_provider", provider=provider)
        return None


def is_llm_available() -> bool:
    """Check whether an LLM provider is configured and presumably reachable."""
    return get_llm_model() is not None
