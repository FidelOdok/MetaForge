"""web.fetch is offered without a search key; web.search only with one.

Reading a page a person names needs no search provider. Hiding web.fetch
behind BRAVE_API_KEY left a hosted Imagine studio unable to read a supplier
page it was given, so it could not record a sourced component at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.web.mcp_adapter import WebMcpServer


class _Provider:
    name = "fake"

    async def search(self, query: str, limit: int = 5) -> list[Any]:
        return []

    async def close(self) -> None:
        return None


def test_without_a_provider_only_fetch_is_offered() -> None:
    server = WebMcpServer(provider=None)
    assert set(server.tool_ids) == {"web.fetch"}


def test_with_a_provider_both_are_offered() -> None:
    server = WebMcpServer(provider=_Provider())  # type: ignore[arg-type]
    assert set(server.tool_ids) == {"web.search", "web.fetch"}


async def test_close_without_a_provider_is_a_no_op() -> None:
    await WebMcpServer(provider=None).close()


async def test_bootstrap_registers_fetch_without_brave_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_registry import bootstrap

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("METAFORGE_ADAPTERS", raising=False)
    registry = await bootstrap.bootstrap_tool_registry(adapter_ids=["web"])
    assert registry.get_tool("web.fetch") is not None
    assert registry.get_tool("web.search") is None
