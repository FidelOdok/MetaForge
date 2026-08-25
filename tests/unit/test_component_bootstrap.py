"""Unit tests for the ``component``/``offer_resolver`` MCP adapter registration
in ``bootstrap_tool_registry`` (MET-436).

Mirrors ``tests/unit/test_memory_bootstrap.py``'s pattern: use
``adapter_ids=[]`` to isolate the runtime-injected block under test (per
``bootstrap_tool_registry``'s own ``adapter_ids or list(_ADAPTER_REGISTRY.
keys())`` idiom at the top of the function, an empty list is falsy and
behaves the same as omitting ``adapter_ids`` entirely — it does *not* mean
"register nothing").
"""

from __future__ import annotations

from typing import Any

import pytest

from digital_twin.catalog.query import CatalogQuery, CatalogQueryResult
from digital_twin.catalog.store import ComponentCatalogStore
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)


class _FakeStore(ComponentCatalogStore):
    """Bootstrap only constructs + registers — never calls ``query()``."""

    def __init__(self) -> None:
        super().__init__(dsn="postgresql://unused/unused")

    async def query(self, catalog_query: CatalogQuery) -> CatalogQueryResult:  # pragma: no cover
        raise AssertionError("bootstrap wiring should never call query()")


class _FakeKnowledgeService:
    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:  # pragma: no cover
        raise AssertionError("bootstrap wiring should never call search()")

    async def extract_properties(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("bootstrap wiring should never call extract_properties()")


class _FakeIntentLLM:
    async def complete(self, prompt: str) -> str:  # pragma: no cover
        raise AssertionError("bootstrap wiring should never call complete()")


class _FakeDistributorAdapter(DistributorAdapter):
    @property
    def name(self) -> str:
        return "FakeDistributor"

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        return []

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        return None


def _tool_id(tool: Any) -> str:
    for attr in ("tool_id", "id", "name"):
        value = getattr(tool, attr, None)
        if value:
            return str(value)
    if isinstance(tool, dict):
        return str(tool.get("tool_id") or tool.get("id") or tool.get("name", ""))
    return str(tool)


# ---------------------------------------------------------------------------
# component adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_registers_component_adapter_when_all_three_deps_supplied():
    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        component_catalog_store=_FakeStore(),
        knowledge_service=_FakeKnowledgeService(),
        component_intent_llm=_FakeIntentLLM(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "component.search_parametric" in tool_ids
    assert "component.search_intent" in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_skips_component_adapter_without_store():
    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        knowledge_service=_FakeKnowledgeService(),
        component_intent_llm=_FakeIntentLLM(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "component.search_parametric" not in tool_ids
    assert "component.search_intent" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_skips_component_adapter_without_knowledge_service():
    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        component_catalog_store=_FakeStore(),
        component_intent_llm=_FakeIntentLLM(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "component.search_parametric" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_skips_component_adapter_without_llm():
    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        component_catalog_store=_FakeStore(),
        knowledge_service=_FakeKnowledgeService(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "component.search_parametric" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_respects_component_disabled_via_env(monkeypatch):
    monkeypatch.setenv("METAFORGE_ADAPTER_COMPONENT_ENABLED", "false")
    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        component_catalog_store=_FakeStore(),
        knowledge_service=_FakeKnowledgeService(),
        component_intent_llm=_FakeIntentLLM(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "component.search_parametric" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_explicit_adapter_ids_can_exclude_component():
    """An explicit, narrow ``adapter_ids`` list scopes out ``component`` even
    when its three collaborators are all supplied — unlike the other
    runtime-injected blocks (knowledge/memory/etc), which have no such
    scoping check, ``component`` and ``offer_resolver`` are the first two
    with no *required* external dependency, so without this check an
    explicit ``adapter_ids=["cadquery"]``-style request would always pull
    them in regardless of what was actually asked for."""
    registry = await bootstrap_tool_registry(
        adapter_ids=["cadquery"],
        component_catalog_store=_FakeStore(),
        knowledge_service=_FakeKnowledgeService(),
        component_intent_llm=_FakeIntentLLM(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert not any(t.startswith("component.") for t in tool_ids)


# ---------------------------------------------------------------------------
# offer_resolver adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_registers_offer_resolver_with_zero_distributors():
    """No required collaborator — registers even with no distributor
    credentials configured, since "no offers found" is its normal
    degraded response, not a missing-tool situation."""
    registry = await bootstrap_tool_registry(adapter_ids=[])
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "distributors.resolve_offers" in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_respects_offer_resolver_disabled_via_env(monkeypatch):
    monkeypatch.setenv("METAFORGE_ADAPTER_OFFER_RESOLVER_ENABLED", "false")
    registry = await bootstrap_tool_registry(adapter_ids=[])
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "distributors.resolve_offers" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_explicit_adapter_ids_can_exclude_offer_resolver():
    registry = await bootstrap_tool_registry(adapter_ids=["cadquery"])
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "distributors.resolve_offers" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_offer_resolver_available_with_a_configured_distributor(monkeypatch):
    """When at least one distributor is configured, offer_resolver still
    registers as one tool alongside it (not one-per-distributor) — it fans
    out internally across whatever ``_available_distributor_adapters``
    ended up populated during the distributor loop."""
    monkeypatch.setenv("MOUSER_API_KEY", "fake-key-for-bootstrap-wiring-test")
    registry = await bootstrap_tool_registry(adapter_ids=[])
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "mouser.search" in tool_ids
    assert "distributors.resolve_offers" in tool_ids
