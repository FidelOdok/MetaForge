"""Where the MCP sidecar's OAuth state outlives the process (FORGE-697).

:class:`~metaforge.mcp.oauth.OAuthProvider` answers every request from
memory. Before this, memory was all there was: a restart forgot every
registered client and token, and a client such as Claude Code, which keeps
reusing the ``client_id`` it registered, got ``invalid_client`` on
``/authorize`` and could not sign in again at all.

:class:`OAuthPersistence` is what the provider loads once at startup and
writes through to on every change. Registered clients and issued access /
refresh tokens are kept; tokens by their SHA-256 only, so a database read
never yields a live credential. Authorization codes are not kept: they live
for minutes, and a restart mid-login only means logging in again.

:class:`PgOAuthPersistence` uses the same ``DATABASE_URL`` engine the
sidecar's agent-session store does. :class:`InMemoryOAuthPersistence` is the
local-dev default and the test double.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "ClientRecord",
    "InMemoryOAuthPersistence",
    "OAuthPersistence",
    "PgOAuthPersistence",
    "TokenRecord",
    "create_oauth_persistence",
]


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    redirect_uris: tuple[str, ...]
    client_name: str
    issued_at: int


@dataclass(frozen=True)
class TokenRecord:
    token_hash: str
    #: ``access`` or ``refresh``.
    kind: str
    actor_id: str
    scope: str
    client_id: str
    expires_at: float


class OAuthPersistence(Protocol):
    async def load(self, now: float) -> tuple[list[ClientRecord], list[TokenRecord]]:
        """Every client, and every token still live at ``now`` (expired ones are purged)."""
        ...

    async def save_client(self, client: ClientRecord) -> None: ...

    async def save_token(self, token: TokenRecord) -> None: ...

    async def delete_token(self, token_hash: str) -> None: ...


class InMemoryOAuthPersistence:
    """Persistence that lasts as long as this object: local dev and tests."""

    def __init__(self) -> None:
        self.clients: dict[str, ClientRecord] = {}
        self.tokens: dict[str, TokenRecord] = {}

    async def load(self, now: float) -> tuple[list[ClientRecord], list[TokenRecord]]:
        self.tokens = {h: t for h, t in self.tokens.items() if t.expires_at >= now}
        return list(self.clients.values()), list(self.tokens.values())

    async def save_client(self, client: ClientRecord) -> None:
        self.clients[client.client_id] = client

    async def save_token(self, token: TokenRecord) -> None:
        self.tokens[token.token_hash] = token

    async def delete_token(self, token_hash: str) -> None:
        self.tokens.pop(token_hash, None)


class PgOAuthPersistence:
    """``mcp_oauth_clients`` / ``mcp_oauth_tokens`` through SQLAlchemy async."""

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine

    async def ensure_tables(self) -> None:
        """Create the two tables if missing; the sidecar may start before the gateway."""
        from api_gateway.db.engine import get_engine
        from api_gateway.db.models import Base

        eng = self._engine or get_engine()
        if eng is None:
            raise RuntimeError("No database engine available")
        tables = [Base.metadata.tables[n] for n in ("mcp_oauth_clients", "mcp_oauth_tokens")]
        async with eng.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))

    async def load(self, now: float) -> tuple[list[ClientRecord], list[TokenRecord]]:
        from sqlalchemy import delete, select

        from api_gateway.db.engine import get_session
        from api_gateway.db.models import McpOAuthClientRow, McpOAuthTokenRow

        async with get_session(self._engine) as session:
            await session.execute(delete(McpOAuthTokenRow).where(McpOAuthTokenRow.expires_at < now))
            clients = [
                ClientRecord(
                    client_id=row.client_id,
                    redirect_uris=tuple(row.redirect_uris or ()),
                    client_name=row.client_name,
                    issued_at=row.issued_at,
                )
                for row in (await session.scalars(select(McpOAuthClientRow))).all()
            ]
            tokens = [
                TokenRecord(
                    token_hash=row.token_hash,
                    kind=row.kind,
                    actor_id=row.actor_id,
                    scope=row.scope,
                    client_id=row.client_id,
                    expires_at=row.expires_at,
                )
                for row in (await session.scalars(select(McpOAuthTokenRow))).all()
            ]
        return clients, tokens

    async def save_client(self, client: ClientRecord) -> None:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import McpOAuthClientRow

        async with get_session(self._engine) as session:
            await session.merge(
                McpOAuthClientRow(
                    client_id=client.client_id,
                    redirect_uris=list(client.redirect_uris),
                    client_name=client.client_name,
                    issued_at=client.issued_at,
                )
            )

    async def save_token(self, token: TokenRecord) -> None:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import McpOAuthTokenRow

        async with get_session(self._engine) as session:
            await session.merge(
                McpOAuthTokenRow(
                    token_hash=token.token_hash,
                    kind=token.kind,
                    actor_id=token.actor_id,
                    scope=token.scope,
                    client_id=token.client_id,
                    expires_at=token.expires_at,
                )
            )

    async def delete_token(self, token_hash: str) -> None:
        from sqlalchemy import delete

        from api_gateway.db.engine import get_session
        from api_gateway.db.models import McpOAuthTokenRow

        async with get_session(self._engine) as session:
            await session.execute(
                delete(McpOAuthTokenRow).where(McpOAuthTokenRow.token_hash == token_hash)
            )


async def create_oauth_persistence() -> OAuthPersistence | None:
    """Postgres when ``DATABASE_URL`` gives an engine, else ``None`` (memory only).

    A database that is configured but unreachable degrades to memory with a
    warning rather than stopping the sidecar: sign-ins then last until the
    next restart, as they did before FORGE-697.
    """
    try:
        from api_gateway.db import HAS_SQLALCHEMY
        from api_gateway.db.engine import get_engine

        if not HAS_SQLALCHEMY or get_engine() is None:
            logger.info("mcp_oauth_store_in_memory")
            return None
        store = PgOAuthPersistence()
        await store.ensure_tables()
        logger.info("mcp_oauth_store_pg")
        return store
    except Exception as exc:  # noqa: BLE001 -- degrade, never block startup
        logger.warning("mcp_oauth_store_pg_failed_fallback", error=str(exc))
        return None
