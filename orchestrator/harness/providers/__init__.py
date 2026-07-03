"""Provider pipeline for the robust harness (MET-547, Phase 1).

Provider-agnostic model resolution with bounded retries and ordered
fallback, plus role-based model slots. The SDK binding is injected, so
this package depends only on stdlib + observability and stays fully
unit-testable without touching the network.
"""

from __future__ import annotations

from orchestrator.harness.providers.adapters import (
    anthropic_invoke,
    bedrock_invoke,
    codex_invoke,
    default_invoke,
    gemini_invoke,
    openai_invoke,
)
from orchestrator.harness.providers.config import (
    ConfigError,
    HarnessProviderConfig,
    load_provider_config,
)
from orchestrator.harness.providers.credentials import (
    Credential,
    CredentialStore,
    default_credentials_path,
)
from orchestrator.harness.providers.pipeline import (
    AllProvidersFailedError,
    ProviderError,
    ProviderPipeline,
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
)
from orchestrator.harness.providers.registry import (
    ProviderProfile,
    UnknownProviderError,
    available_providers,
    get_profile,
    resolve_provider,
)
from orchestrator.harness.providers.rotation import (
    AuthProfile,
    ProfileExhaustedError,
    ProfileRotor,
    rotating_invoke,
)

__all__ = [
    "AllProvidersFailedError",
    "AuthProfile",
    "ConfigError",
    "Credential",
    "CredentialStore",
    "default_credentials_path",
    "anthropic_invoke",
    "bedrock_invoke",
    "codex_invoke",
    "default_invoke",
    "gemini_invoke",
    "openai_invoke",
    "HarnessProviderConfig",
    "ProfileExhaustedError",
    "ProfileRotor",
    "ProviderError",
    "ProviderPipeline",
    "ProviderProfile",
    "ProviderSpec",
    "RetryPolicy",
    "RoleModelSlots",
    "UnknownProviderError",
    "available_providers",
    "get_profile",
    "load_provider_config",
    "resolve_provider",
    "rotating_invoke",
]
