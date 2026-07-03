"""Harness runtime composition root (MET-547, Phase 2/3 seam).

One object that bundles the services a run needs — the provider pipeline
(model access), the tool registry (native + MCP), and the run store — so the
Planner/Generator/Evaluator agents receive a single ``HarnessRuntime`` instead
of reaching for globals. Gate enforcement is centralized here: ``call_tool``
always threads the runtime's ``gate_check`` into the registry, so a consequential
tool can't be invoked through the runtime without its preconditions being
evaluated.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from orchestrator.harness.providers import (
    CredentialStore,
    HarnessProviderConfig,
    ProviderPipeline,
    RetryPolicy,
    RoleModelSlots,
    store_backed_invoke,
)
from orchestrator.harness.providers.pipeline import Invoke, ProviderSpec
from orchestrator.harness.runs import InMemoryRunStore
from orchestrator.harness.tools import GateCheck, ToolRegistry


@dataclass
class HarnessRuntime:
    """The services a harness run is given: models, tools, run state."""

    providers: ProviderPipeline
    tools: ToolRegistry
    runs: InMemoryRunStore
    gate_check: GateCheck | None = None
    # Optional multi-credential store: when set, model calls rotate a provider's
    # stored credentials per session and blacklist any that fail terminally.
    credentials: CredentialStore | None = None
    session_id: str = "default"

    @classmethod
    def build(
        cls,
        provider_config: HarnessProviderConfig | None = None,
        *,
        tools: ToolRegistry | None = None,
        gate_check: GateCheck | None = None,
        credentials: CredentialStore | None = None,
        session_id: str = "default",
        clock: Callable[[], float] = time.time,
    ) -> HarnessRuntime:
        """Assemble a runtime from an optional provider config.

        With no config the provider pipeline has no role slots (resolving a
        role raises) — useful for tool-only runtimes in tests.
        """
        slots = provider_config.slots if provider_config else RoleModelSlots()
        retry = provider_config.retry if provider_config else RetryPolicy()
        return cls(
            providers=ProviderPipeline(slots, retry_policy=retry),
            tools=tools or ToolRegistry(),
            runs=InMemoryRunStore(clock=clock),
            gate_check=gate_check,
            credentials=credentials,
            session_id=session_id,
        )

    def _effective_invoke(self, base_invoke: Invoke) -> Invoke:
        """Wrap ``base_invoke`` with credential rotation when a store is set.

        For each provider the pipeline tries, if the store has healthy
        credentials for that provider id, calls rotate through them (and dead
        ones are blacklisted); otherwise the base invoke is used unchanged.
        """
        store = self.credentials
        if store is None:
            return base_invoke
        session = self.session_id

        async def invoke(spec: ProviderSpec, request: Any) -> Any:
            if store.healthy(spec.name):
                rotated = store_backed_invoke(base_invoke, store, spec.name, session)
                return await rotated(spec, request)
            return await base_invoke(spec, request)

        return invoke

    async def complete(self, role: str, request: Any, invoke: Invoke) -> Any:
        """Run a model request for a role through the provider pipeline."""
        return await self.providers.complete(role, request, self._effective_invoke(invoke))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a registered tool, enforcing this runtime's gate policy."""
        return await self.tools.invoke(name, arguments, gate_check=self.gate_check)
