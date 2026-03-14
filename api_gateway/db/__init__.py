"""PostgreSQL database layer for MetaForge API Gateway.

Provides SQLAlchemy async models, a repository layer matching the in-memory
``ChatStore`` interface, and connection-pool management via ``DATABASE_URL``.

When ``sqlalchemy`` is not installed or ``DATABASE_URL`` is unset the module
gracefully degrades — callers can detect availability via :data:`HAS_SQLALCHEMY`.
"""

from __future__ import annotations

HAS_SQLALCHEMY: bool
try:
    import sqlalchemy as _sa  # noqa: F401

    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

__all__ = ["HAS_SQLALCHEMY"]
