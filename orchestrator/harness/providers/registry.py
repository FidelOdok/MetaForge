"""Provider registry — resolve a provider id to a ProviderSpec (MET-549).

Mirrors the Hermes Agent provider surface. Because the pipeline already has
``anthropic_invoke`` (native) and ``openai_invoke`` (OpenAI-compatible, driven
by ``base_url``), the whole OpenAI-compatible family is reachable — this
registry just maps a provider *id* (e.g. ``deepseek``) to its API family, key
env var, and base URL, so callers name a provider instead of hand-building a
ProviderSpec.

Base URLs are set only where they are stable and documented; for providers
whose endpoint is account/region specific (Azure, DashScope, GLM, MiniMax, …)
``base_url`` is ``None`` and read at resolve time from ``base_url_env`` (default
``HARNESS_<ID>_BASE_URL``) — so a guessed URL is never shipped.

``ProviderSpec.name`` is set to the provider id; ``default_invoke`` routes
anthropic-family ids to the native adapter and everything else to the
OpenAI-compatible adapter, so dispatch stays correct.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import structlog

from orchestrator.harness.providers.pipeline import ProviderSpec

logger = structlog.get_logger(__name__)

OPENAI = "openai"
ANTHROPIC = "anthropic"
GEMINI = "gemini"
BEDROCK = "bedrock"


@dataclass(frozen=True)
class ProviderProfile:
    """How to reach one provider."""

    id: str
    api_family: str  # OPENAI | ANTHROPIC
    api_key_env: str
    base_url: str | None = None
    base_url_env: str | None = None  # env to read base_url from when not fixed
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _p(
    id_: str,
    api_key_env: str,
    *,
    family: str = OPENAI,
    base_url: str | None = None,
    base_url_env: str | None = None,
    aliases: tuple[str, ...] = (),
) -> ProviderProfile:
    return ProviderProfile(
        id=id_,
        api_family=family,
        api_key_env=api_key_env,
        base_url=base_url,
        base_url_env=base_url_env or f"HARNESS_{id_.upper().replace('-', '_')}_BASE_URL",
        aliases=aliases,
    )


# Curated from the Hermes provider docs. Fixed base_url only where stable.
_PROFILES: tuple[ProviderProfile, ...] = (
    # Native / SDK-default endpoints
    _p("anthropic", "ANTHROPIC_API_KEY", family=ANTHROPIC, aliases=("claude", "claude-code")),
    _p("openai", "OPENAI_API_KEY", aliases=("openai-api",)),
    _p("gemini", "GOOGLE_API_KEY", family=GEMINI, aliases=("google",)),
    # AWS credential chain (no api key env); base_url derived from region
    _p("bedrock", "AWS_ACCESS_KEY_ID", family=BEDROCK, aliases=("aws-bedrock",)),
    # OpenAI-compatible, stable documented base URLs
    _p("openrouter", "OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1"),
    _p("deepseek", "DEEPSEEK_API_KEY", base_url="https://api.deepseek.com"),
    _p("xai", "XAI_API_KEY", base_url="https://api.x.ai/v1", aliases=("grok",)),
    _p("nvidia", "NVIDIA_API_KEY", base_url="https://integrate.api.nvidia.com/v1"),
    _p("huggingface", "HF_TOKEN", base_url="https://router.huggingface.co/v1", aliases=("hf",)),
    _p("novita", "NOVITA_API_KEY", base_url="https://api.novita.ai/v3/openai"),
    _p(
        "kimi-coding",
        "KIMI_API_KEY",
        base_url="https://api.moonshot.ai/v1",
        aliases=("kimi", "moonshot"),
    ),
    _p(
        "kimi-coding-cn",
        "KIMI_CN_API_KEY",
        base_url="https://api.moonshot.cn/v1",
        aliases=("kimi-cn", "moonshot-cn"),
    ),
    # OpenAI-compatible, account/region-specific base URL (from env)
    _p("zai", "GLM_API_KEY", aliases=("glm",)),
    _p("alibaba", "DASHSCOPE_API_KEY", aliases=("qwen", "dashscope")),
    _p("alibaba-coding-plan", "DASHSCOPE_API_KEY", aliases=("alibaba_coding",)),
    _p("minimax", "MINIMAX_API_KEY"),
    _p("minimax-cn", "MINIMAX_CN_API_KEY"),
    _p("arcee", "ARCEEAI_API_KEY", aliases=("arcee-ai", "arceeai")),
    _p("gmi", "GMI_API_KEY", aliases=("gmi-cloud", "gmicloud")),
    _p("xiaomi", "XIAOMI_API_KEY", aliases=("mimo", "xiaomi-mimo")),
    _p("tencent-tokenhub", "TOKENHUB_API_KEY", aliases=("tencent", "tokenhub")),
    _p("opencode-zen", "OPENCODE_ZEN_API_KEY"),
    _p("opencode-go", "OPENCODE_GO_API_KEY"),
    _p("kilocode", "KILOCODE_API_KEY"),
    _p("stepfun", "STEPFUN_API_KEY"),
    _p("azure-foundry", "AZURE_OPENAI_API_KEY"),
    # Local / self-hosted (stable default localhost endpoints)
    _p("ollama", "OLLAMA_API_KEY", base_url="http://localhost:11434/v1"),
    _p("vllm", "VLLM_API_KEY", base_url="http://localhost:8000/v1"),
    _p("lmstudio", "LMSTUDIO_API_KEY", base_url="http://localhost:1234/v1"),
    _p("sglang", "SGLANG_API_KEY", base_url="http://localhost:30000/v1"),
    _p("llamacpp", "LLAMACPP_API_KEY", base_url="http://localhost:8080/v1", aliases=("llama-cpp",)),
    _p("litellm", "LITELLM_API_KEY", base_url="http://localhost:4000/v1"),
    _p("clawrouter", "CLAWROUTER_API_KEY", base_url="http://localhost:8402/v1"),
    # Generic custom endpoint — base_url must be supplied
    _p("custom", "CUSTOM_API_KEY"),
)

_BY_ID: dict[str, ProviderProfile] = {}
for _profile in _PROFILES:
    _BY_ID[_profile.id] = _profile
    for _alias in _profile.aliases:
        _BY_ID[_alias] = _profile


class UnknownProviderError(KeyError):
    """No registered provider with the given id or alias."""


def available_providers() -> list[str]:
    """Canonical provider ids (not aliases), sorted."""
    return sorted({p.id for p in _PROFILES})


def get_profile(provider_id: str) -> ProviderProfile:
    try:
        return _BY_ID[provider_id.strip().lower()]
    except KeyError as exc:
        raise UnknownProviderError(provider_id) from exc


def resolve_provider(
    provider_id: str,
    model: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> ProviderSpec:
    """Build a ProviderSpec for ``provider_id`` + ``model``.

    base_url precedence: explicit arg > profile fixed base_url > profile
    ``base_url_env`` environment variable > None (SDK default).
    """
    profile = get_profile(provider_id)
    resolved_base = base_url or profile.base_url
    if resolved_base is None and profile.base_url_env:
        resolved_base = os.environ.get(profile.base_url_env, "").strip() or None
    spec = ProviderSpec(
        name=profile.id,
        model=model,
        api_key_env=api_key_env or profile.api_key_env,
        base_url=resolved_base,
    )
    logger.info(
        "provider_resolved",
        provider=profile.id,
        family=profile.api_family,
        has_base_url=resolved_base is not None,
    )
    return spec
