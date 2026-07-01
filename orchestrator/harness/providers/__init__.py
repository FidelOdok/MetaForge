"""Provider pipeline for the robust harness (MET-547, Phase 1).

Provider-agnostic model resolution with bounded retries and ordered
fallback, plus role-based model slots. The SDK binding is injected, so
this package depends only on stdlib + observability and stays fully
unit-testable without touching the network.
"""

from __future__ import annotations

from orchestrator.harness.providers.pipeline import (
    AllProvidersFailedError,
    ProviderError,
    ProviderPipeline,
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
)

__all__ = [
    "AllProvidersFailedError",
    "ProviderError",
    "ProviderPipeline",
    "ProviderSpec",
    "RetryPolicy",
    "RoleModelSlots",
]
