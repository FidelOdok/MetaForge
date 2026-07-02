"""Harness-backed chat turn (MET-548, surface A).

When ``METAFORGE_CHAT_HARNESS`` is enabled, a chat message is answered by the
MET-547 harness — a ReAct loop over a provider pipeline (retry + failover) with
gate-enforced tools — instead of the direct pydantic-ai agent call. The chat
REST/SSE contract is unchanged; only the internals swap, behind the flag, so
the existing dashboard chat UI drives the harness.

``run_chat_turn`` takes an injectable ``invoke`` so it unit-tests without
network; production defaults to the real provider adapters.
"""

from __future__ import annotations

import os

import structlog

from orchestrator.harness import build_agent_runtime
from orchestrator.harness.policy import ModelPolicy
from orchestrator.harness.providers import (
    HarnessProviderConfig,
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
    UnknownProviderError,
    default_invoke,
    resolve_provider,
)
from orchestrator.harness.providers.pipeline import Invoke
from orchestrator.harness.react import run_react

logger = structlog.get_logger(__name__)

_TRUTHY = {"1", "true", "on", "yes"}


def chat_harness_enabled() -> bool:
    """True when chat should route through the harness (env flag)."""
    return os.environ.get("METAFORGE_CHAT_HARNESS", "").strip().lower() in _TRUTHY


def provider_config_from_env() -> HarnessProviderConfig:
    """Build a single-role provider config from the METAFORGE_LLM_* env vars."""
    provider = (os.environ.get("METAFORGE_LLM_PROVIDER") or "anthropic").strip().lower()
    model = (os.environ.get("METAFORGE_LLM_MODEL") or "claude-opus-4-8").strip()
    base_url = (os.environ.get("METAFORGE_LLM_BASE_URL") or "").strip() or None
    # Resolve known provider ids through the registry (fills base_url from the
    # provider's documented endpoint); the single METAFORGE_LLM_API_KEY is the
    # key env, and an explicit METAFORGE_LLM_BASE_URL still overrides.
    try:
        spec = resolve_provider(
            provider, model, base_url=base_url, api_key_env="METAFORGE_LLM_API_KEY"
        )
    except UnknownProviderError:
        spec = ProviderSpec(
            name=provider, model=model, api_key_env="METAFORGE_LLM_API_KEY", base_url=base_url
        )
    return HarnessProviderConfig(
        slots=RoleModelSlots(slots={"generator": [spec]}), retry=RetryPolicy(), rotor=None
    )


async def run_chat_turn(
    user_content: str, *, invoke: Invoke = default_invoke, max_steps: int = 6
) -> str:
    """Answer a chat message via the harness ReAct loop. Returns the reply text."""
    ctx = build_agent_runtime(provider_config_from_env())
    policy = ModelPolicy(ctx.runtime, role="generator", invoke=invoke)
    result = await run_react(ctx.runtime, policy, user_content, max_steps=max_steps)
    logger.info("chat_harness_turn", status=result.status, steps=len(result.steps))
    if result.status == "completed":
        return str(result.output)
    return "I couldn't converge on an answer within the step budget."
