"""Async engine factory with connection pooling and graceful fallback.

Reads ``DATABASE_URL`` from the environment.  When the variable is unset
or ``sqlalchemy`` is not installed, :func:`get_engine` returns ``None``
and :func:`is_pg_available` returns ``False``.

Usage::

    engine = get_engine()
    if engine is not None:
        async with get_session(engine) as session:
            ...
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

from observability.tracing import get_tracer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.db.engine")

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None


def get_database_url() -> str | None:
    """Return the configured ``DATABASE_URL``, or ``None``."""
    return os.environ.get("DATABASE_URL")


def _create_engine(url: str) -> AsyncEngine | None:
    """Create an async engine with sensible pool defaults."""
    with tracer.start_as_current_span("db.create_engine") as span:
        try:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine: AsyncEngine = create_async_engine(
                url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=1800,
                echo=False,
            )
            span.set_attribute("db.pool_size", 5)
            span.set_attribute("db.max_overflow", 10)
            logger.info(
                "pg_engine_created",
                pool_size=5,
                max_overflow=10,
            )
            return engine
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("pg_engine_creation_failed", error=str(exc))
            return None


def get_engine() -> AsyncEngine | None:
    """Return the module-level async engine, creating it lazily.

    Returns ``None`` when ``DATABASE_URL`` is not set or when
    ``sqlalchemy`` is not installed.
    """
    global _engine  # noqa: PLW0603
    if _engine is not None:
        return _engine

    url = get_database_url()
    if url is None:
        logger.debug("pg_engine_skipped", reason="DATABASE_URL not set")
        return None

    try:
        _engine = _create_engine(url)
    except Exception:
        _engine = None
    return _engine


def is_pg_available() -> bool:
    """Return ``True`` if PostgreSQL is configured and reachable."""
    return get_engine() is not None


async def dispose_engine() -> None:
    """Dispose the current engine (used during shutdown)."""
    global _engine  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("pg_engine_disposed")


@asynccontextmanager
async def get_session(engine: Any | None = None) -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` bound to *engine* (or the module engine).

    Raises ``RuntimeError`` when no engine is available.
    """
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker

    eng = engine or get_engine()
    if eng is None:
        raise RuntimeError("No database engine available")

    factory = async_sessionmaker(eng, class_=_AsyncSession, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
