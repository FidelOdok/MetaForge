"""Postgres connection settings shared by every pool MetaForge opens.

A service opens several pools against the same database: the SQLAlchemy
engine (``api_gateway.db.engine``) and one asyncpg pool per store (catalog,
knowledge, experience memory, insights). Behind a connection pooler with a
small client limit, such as Supabase's session-mode pooler (15 clients on the
smallest instance), the idle connections those pools keep open are enough to
exhaust it, and every request then fails with ``EMAXCONNSESSION``.

Two settings let a deployment fit:

``METAFORGE_PG_STATEMENT_CACHE=off``
    Turn off asyncpg's prepared-statement cache. Required behind a
    transaction-mode pooler (Supabase port 6543, PgBouncer
    ``pool_mode=transaction``), where a server connection is not kept between
    transactions and a cached named statement may not exist on the next one.
    The transaction pooler shares a few server connections between many
    clients, so it is the fix for a small client limit.

``METAFORGE_PG_STORE_POOL_MAX``
    The largest size of each store's asyncpg pool (default: the store's own).

Both default to today's behaviour, so a local Postgres is unaffected.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

STATEMENT_CACHE_ENV = "METAFORGE_PG_STATEMENT_CACHE"
STORE_POOL_MAX_ENV = "METAFORGE_PG_STORE_POOL_MAX"

_OFF = {"off", "0", "false", "no", "disabled"}


def statement_cache_enabled() -> bool:
    """Whether asyncpg may cache prepared statements (default yes)."""
    return os.environ.get(STATEMENT_CACHE_ENV, "").strip().lower() not in _OFF


def asyncpg_connect_kwargs() -> dict[str, Any]:
    """Keyword arguments for ``asyncpg.connect`` / ``asyncpg.create_pool``."""
    return {} if statement_cache_enabled() else {"statement_cache_size": 0}


def store_pool_kwargs(default_max: int) -> dict[str, Any]:
    """Keyword arguments for a store's ``asyncpg.create_pool``.

    ``default_max`` is the store's own maximum; ``METAFORGE_PG_STORE_POOL_MAX``
    lowers (or raises) it for every store at once. A malformed value is
    ignored with a warning rather than failing the store.
    """
    max_size = default_max
    raw = os.environ.get(STORE_POOL_MAX_ENV, "").strip()
    if raw:
        try:
            max_size = max(1, int(raw))
        except ValueError:
            logger.warning("pg_store_pool_max_invalid", value=raw, using=default_max)
    return {"min_size": 1, "max_size": max_size, **asyncpg_connect_kwargs()}


def sqlalchemy_connect_args() -> dict[str, Any]:
    """``connect_args`` for SQLAlchemy's asyncpg dialect.

    The dialect keeps its own prepared-statement cache on top of asyncpg's, so
    both are turned off together.
    """
    if statement_cache_enabled():
        return {}
    return {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
