"""OAuth state survives a restart of the MCP sidecar (FORGE-697).

A restart used to forget every registered client and token. Claude Code
keeps reusing the ``client_id`` it registered, so after a deploy it got
``invalid_client`` on ``/authorize`` and could not sign in again at all.

Each test plays two processes: one provider registers and issues, a second
one on the same persistence loads and must honour what the first issued.
Postgres is stood in for by SQLite through the same SQLAlchemy rows.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest

from metaforge.mcp.oauth import OAuthConfig, OAuthError, OAuthProvider
from metaforge.mcp.oauth_store import (
    InMemoryOAuthPersistence,
    OAuthPersistence,
    PgOAuthPersistence,
)

CB = "https://claude.ai/cb"


def _pkce() -> tuple[str, str]:
    verifier = "verifier-" + "b" * 50
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def _authorize(p: OAuthProvider, client_id: str, challenge: str) -> Any:
    return p.validate_authorize(
        client_id=client_id,
        redirect_uri=CB,
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
    )


async def _sign_in(p: OAuthProvider) -> tuple[str, dict[str, Any]]:
    """Register, authorize and exchange, as a client does; returns (client_id, tokens)."""
    verifier, challenge = _pkce()
    reg = p.register_client({"redirect_uris": [CB]})
    client = _authorize(p, str(reg["client_id"]), challenge)
    code = p.issue_code(client, CB, challenge, "mcp", operator="ana")
    tokens = p.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CB,
            "client_id": str(reg["client_id"]),
            "code_verifier": verifier,
        }
    )
    await p.flush()
    return str(reg["client_id"]), tokens


async def _restarted(store: OAuthPersistence, **kw: Any) -> OAuthProvider:
    p = OAuthProvider(OAuthConfig(login_secret="s3cret", **kw), persistence=store)
    await p.load()
    return p


async def _sql_store(tmp_path: Path) -> tuple[PgOAuthPersistence, Any]:
    sqlalchemy = pytest.importorskip("sqlalchemy.ext.asyncio")
    pytest.importorskip("aiosqlite")
    engine = sqlalchemy.create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oauth.db'}")
    sql = PgOAuthPersistence(engine)
    await sql.ensure_tables()
    return sql, engine


@pytest.fixture
async def pg_store(tmp_path: Path) -> Any:
    sql, engine = await _sql_store(tmp_path)
    yield sql
    await engine.dispose()


@pytest.fixture(params=["memory", "pg"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "memory":
        yield InMemoryOAuthPersistence()
        return
    sql, engine = await _sql_store(tmp_path)
    yield sql
    await engine.dispose()


class TestRestart:
    async def test_a_registered_client_can_still_sign_in(self, store: Any) -> None:
        first = OAuthProvider(OAuthConfig(login_secret="s3cret"), persistence=store)
        client_id, _ = await _sign_in(first)

        second = await _restarted(store)
        _verifier, challenge = _pkce()
        assert _authorize(second, client_id, challenge).client_id == client_id

    async def test_an_issued_access_token_still_works(self, store: Any) -> None:
        first = OAuthProvider(OAuthConfig(login_secret="s3cret"), persistence=store)
        _, tokens = await _sign_in(first)

        second = await _restarted(store)
        assert second.validate_token(str(tokens["access_token"])) == "user:ana"

    async def test_a_refresh_token_still_refreshes_once(self, store: Any) -> None:
        first = OAuthProvider(OAuthConfig(login_secret="s3cret"), persistence=store)
        client_id, tokens = await _sign_in(first)

        second = await _restarted(store)
        grant = {
            "grant_type": "refresh_token",
            "refresh_token": str(tokens["refresh_token"]),
            "client_id": client_id,
        }
        fresh = second.exchange(grant)
        await second.flush()
        assert second.validate_token(str(fresh["access_token"])) == "user:ana"

        # The used refresh token is gone for the next process too.
        third = await _restarted(store)
        with pytest.raises(OAuthError, match="unknown refresh token"):
            third.exchange(grant)
        assert third.validate_token(str(fresh["access_token"])) == "user:ana"

    async def test_an_unknown_client_is_still_refused(self, store: Any) -> None:
        first = OAuthProvider(OAuthConfig(login_secret="s3cret"), persistence=store)
        await _sign_in(first)

        second = await _restarted(store)
        _verifier, challenge = _pkce()
        with pytest.raises(OAuthError) as exc:
            _authorize(second, "mcp-never-registered", challenge)
        assert exc.value.error == "invalid_client"
        assert exc.value.redirectable is False

    async def test_expired_tokens_are_not_brought_back(self, store: Any) -> None:
        clock = {"t": 1000.0}
        first = OAuthProvider(
            OAuthConfig(login_secret="s3cret", access_ttl=10),
            persistence=store,
            now=lambda: clock["t"],
        )
        _, tokens = await _sign_in(first)

        clock["t"] += 60
        second = OAuthProvider(
            OAuthConfig(login_secret="s3cret", access_ttl=10),
            persistence=store,
            now=lambda: clock["t"],
        )
        await second.load()
        assert second.validate_token(str(tokens["access_token"])) is None


async def test_the_database_never_holds_a_raw_token(pg_store: PgOAuthPersistence) -> None:
    first = OAuthProvider(OAuthConfig(login_secret="s3cret"), persistence=pg_store)
    _, tokens = await _sign_in(first)
    _clients, saved = await pg_store.load(0.0)
    hashes = {t.token_hash for t in saved}
    assert str(tokens["access_token"]) not in hashes
    assert str(tokens["refresh_token"]) not in hashes
    assert hashlib.sha256(str(tokens["access_token"]).encode()).hexdigest() in hashes


async def test_a_failing_store_never_fails_the_sign_in() -> None:
    class Broken(InMemoryOAuthPersistence):
        async def save_client(self, client: Any) -> None:
            raise RuntimeError("database down")

        async def save_token(self, token: Any) -> None:
            raise RuntimeError("database down")

    p = OAuthProvider(OAuthConfig(login_secret="s3cret"), persistence=Broken())
    _, tokens = await _sign_in(p)
    assert p.validate_token(str(tokens["access_token"])) == "user:ana"


def test_without_persistence_nothing_changes_outside_a_loop() -> None:
    """Local dev, no DATABASE_URL: the provider works as it always did."""
    p = OAuthProvider(OAuthConfig(login_secret="s3cret"))
    reg = p.register_client({"redirect_uris": [CB]})
    _verifier, challenge = _pkce()
    assert _authorize(p, str(reg["client_id"]), challenge)
