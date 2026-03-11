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

    artifacts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Artifacts produced or modified by the agent",
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


def get_llm_model() -> str | None:
    """Resolve the LLM model string from environment variables.

    Returns a PydanticAI-compatible model string like 'openai:gpt-4o'
    or None if the LLM provider is not configured.
    """
    provider = os.environ.get("METAFORGE_LLM_PROVIDER", "").strip().lower()
    if not provider:
        return None

    model_name = os.environ.get("METAFORGE_LLM_MODEL", "").strip()

    if provider == "openai":
        return f"openai:{model_name or 'gpt-4o'}"
    elif provider == "anthropic":
        return f"anthropic:{model_name or 'claude-sonnet-4-20250514'}"
    else:
        logger.warning("unknown_llm_provider", provider=provider)
        return None


def is_llm_available() -> bool:
    """Check whether an LLM provider is configured and presumably reachable."""
    return get_llm_model() is not None
