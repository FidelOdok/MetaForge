"""Postgres pool settings shared by every pool (behind a connection pooler)."""

from __future__ import annotations

import pytest

from shared import pg


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pg.STATEMENT_CACHE_ENV, raising=False)
    monkeypatch.delenv(pg.STORE_POOL_MAX_ENV, raising=False)


def test_defaults_leave_pools_as_they_were() -> None:
    assert pg.statement_cache_enabled()
    assert pg.asyncpg_connect_kwargs() == {}
    assert pg.sqlalchemy_connect_args() == {}
    assert pg.store_pool_kwargs(10) == {"min_size": 1, "max_size": 10}


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no", "disabled"])
def test_transaction_pooler_turns_off_every_statement_cache(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(pg.STATEMENT_CACHE_ENV, value)
    assert not pg.statement_cache_enabled()
    assert pg.asyncpg_connect_kwargs() == {"statement_cache_size": 0}
    assert pg.sqlalchemy_connect_args() == {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }
    assert pg.store_pool_kwargs(5)["statement_cache_size"] == 0


def test_store_pool_max_caps_every_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pg.STORE_POOL_MAX_ENV, "2")
    assert pg.store_pool_kwargs(10)["max_size"] == 2
    monkeypatch.setenv(pg.STORE_POOL_MAX_ENV, "0")
    assert pg.store_pool_kwargs(10)["max_size"] == 1


def test_a_malformed_cap_keeps_the_store_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pg.STORE_POOL_MAX_ENV, "lots")
    assert pg.store_pool_kwargs(7)["max_size"] == 7


def test_engine_passes_the_connect_args(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("sqlalchemy")
    import sqlalchemy.ext.asyncio as sa_async

    from api_gateway.db import engine as engine_mod

    seen: dict[str, object] = {}

    def fake_create(url: str, **kw: object) -> object:
        seen.update(kw)
        return object()

    monkeypatch.setattr(sa_async, "create_async_engine", fake_create)
    monkeypatch.setenv(pg.STATEMENT_CACHE_ENV, "off")
    assert engine_mod._create_engine("postgresql+asyncpg://u:p@h:6543/db") is not None
    assert seen["connect_args"] == {"statement_cache_size": 0, "prepared_statement_cache_size": 0}


def test_lightrag_pool_follows_the_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService

    for key in (
        "POSTGRES_STATEMENT_CACHE_SIZE",
        "POSTGRES_MAX_CONNECTIONS",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(pg.STATEMENT_CACHE_ENV, "off")
    monkeypatch.setenv(pg.STORE_POOL_MAX_ENV, "3")
    LightRAGKnowledgeService._apply_postgres_env("postgresql://u:p@pooler.example:6543/postgres")
    import os

    assert os.environ["POSTGRES_STATEMENT_CACHE_SIZE"] == "0"
    assert os.environ["POSTGRES_MAX_CONNECTIONS"] == "3"
    assert os.environ["POSTGRES_PORT"] == "6543"
