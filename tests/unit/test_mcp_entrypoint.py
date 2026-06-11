"""Unit tests for ``metaforge.mcp.__main__`` bootstrap helpers (MET-433).

Covers ``_build_knowledge_service`` — the bootstrap gap closer. The
broader transport/auth surface lives in ``test_mcp_server`` and
``test_mcp_transport_auth``; this file owns the env-driven service
construction path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metaforge.mcp.__main__ import (
    _build_knowledge_service,
    _close_knowledge_service,
    _parse_args,
)


class TestBuildKnowledgeService:
    async def test_returns_none_when_database_url_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert await _build_knowledge_service() is None

    async def test_constructs_lightrag_with_normalised_dsn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``postgresql+asyncpg://`` is stripped to ``postgresql://`` for LightRAG.

        Gateway publishes the SQLAlchemy form; LightRAG's pgvector
        client speaks raw libpq. Same translation MET-433 needs the
        standalone MCP to perform.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/forge")
        monkeypatch.delenv("KNOWLEDGE_RERANKER_ENABLED", raising=False)
        monkeypatch.delenv("METAFORGE_LIGHTRAG_WORKDIR", raising=False)

        fake_service: Any = AsyncMock()
        with patch(
            "digital_twin.knowledge.create_knowledge_service",
            return_value=fake_service,
        ) as mock_factory:
            result = await _build_knowledge_service()

        assert result is fake_service
        mock_factory.assert_called_once()
        _, kwargs = mock_factory.call_args
        assert kwargs["postgres_dsn"] == "postgresql://u:p@db:5432/forge"
        assert kwargs["reranker_enabled"] is False
        assert kwargs["working_dir"] == "./.lightrag-storage"
        fake_service.initialize.assert_awaited_once()

    async def test_reranker_env_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/forge")
        monkeypatch.setenv("KNOWLEDGE_RERANKER_ENABLED", "true")
        with patch(
            "digital_twin.knowledge.create_knowledge_service",
            return_value=AsyncMock(),
        ) as mock_factory:
            await _build_knowledge_service()
        _, kwargs = mock_factory.call_args
        assert kwargs["reranker_enabled"] is True

    async def test_workdir_env_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/forge")
        monkeypatch.setenv("METAFORGE_LIGHTRAG_WORKDIR", "/tmp/custom-rag")
        with patch(
            "digital_twin.knowledge.create_knowledge_service",
            return_value=AsyncMock(),
        ) as mock_factory:
            await _build_knowledge_service()
        _, kwargs = mock_factory.call_args
        assert kwargs["working_dir"] == "/tmp/custom-rag"

    async def test_swallows_init_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed init must not take down the rest of the MCP surface.

        The standalone MCP serves cadquery/freecad/calculix/twin/project
        tools too. Postgres unavailable on a dev box shouldn't blank
        all of those out — log a warning and continue with knowledge
        disabled, mirroring the gateway's contract.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/forge")

        failing = AsyncMock()
        failing.initialize.side_effect = RuntimeError("db unreachable")

        with patch(
            "digital_twin.knowledge.create_knowledge_service",
            return_value=failing,
        ):
            result = await _build_knowledge_service()

        assert result is None


class TestCloseKnowledgeService:
    async def test_noop_on_none(self) -> None:
        # Must accept None without crashing — bootstrap returns None
        # when DATABASE_URL is unset.
        await _close_knowledge_service(None)

    async def test_calls_close_when_present(self) -> None:
        svc = AsyncMock()
        await _close_knowledge_service(svc)
        svc.close.assert_awaited_once()

    async def test_swallows_close_failures(self) -> None:
        svc = AsyncMock()
        svc.close.side_effect = RuntimeError("driver already shut down")
        # Must not raise — teardown failures are best-effort.
        await _close_knowledge_service(svc)


class TestAllowTwinMutationsFlag:
    """``--allow-twin-mutations`` CLI flag → twin adapter (MET-488)."""

    def test_flag_absent_defaults_false(self) -> None:
        # Read-only twin is the safe default; the flag must opt in.
        assert _parse_args(["--transport", "http"]).allow_twin_mutations is False

    def test_flag_sets_true(self) -> None:
        args = _parse_args(["--transport", "http", "--allow-twin-mutations"])
        assert args.allow_twin_mutations is True


class TestBuildUnifiedServerTwinMutations:
    """``build_unified_server`` forwards the flag to ``bootstrap_tool_registry``."""

    async def test_forwards_allow_mutations_true(self) -> None:
        from metaforge.mcp import server as server_mod

        fake_registry = MagicMock()
        fake_registry.list_adapter_servers.return_value = []
        with patch.object(
            server_mod,
            "bootstrap_tool_registry",
            AsyncMock(return_value=fake_registry),
        ) as mock_boot:
            await server_mod.build_unified_server(twin_allow_mutations=True)

        _, kwargs = mock_boot.call_args
        assert kwargs["twin_allow_mutations"] is True

    async def test_defaults_to_readonly(self) -> None:
        from metaforge.mcp import server as server_mod

        fake_registry = MagicMock()
        fake_registry.list_adapter_servers.return_value = []
        with patch.object(
            server_mod,
            "bootstrap_tool_registry",
            AsyncMock(return_value=fake_registry),
        ) as mock_boot:
            await server_mod.build_unified_server()

        _, kwargs = mock_boot.call_args
        assert kwargs["twin_allow_mutations"] is False
