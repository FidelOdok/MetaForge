"""Unit tests for ``mcp_core.context`` (MET-387)."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcp_core.context import (
    ENV_ACTOR,
    ENV_CORRELATION,
    ENV_PROJECT,
    ENV_SESSION,
    HEADER_ACTOR,
    HEADER_CORRELATION,
    HEADER_PROJECT,
    HEADER_SESSION,
    McpCallContext,
    context_from_env,
    context_from_headers,
    context_to_headers,
    current_context,
    reset_context,
    set_context,
    with_context,
)

# ---------------------------------------------------------------------------
# Defaults / sentinel
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_context_is_unattributed(self) -> None:
        ctx = McpCallContext()
        assert ctx.project_id is None
        assert ctx.actor_id == "system:unattributed"
        # session_id and correlation_id auto-generate.
        assert isinstance(ctx.session_id, UUID)
        assert isinstance(ctx.correlation_id, UUID)

    def test_default_context_is_frozen(self) -> None:
        ctx = McpCallContext()
        with pytest.raises((TypeError, ValueError)):
            ctx.actor_id = "user:fidel"  # type: ignore[misc]

    def test_default_session_ids_are_unique_per_instance(self) -> None:
        a = McpCallContext()
        b = McpCallContext()
        assert a.session_id != b.session_id


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


class TestHeaderParsing:
    def test_full_header_set(self) -> None:
        project = "11111111-1111-1111-1111-111111111111"
        session = "22222222-2222-2222-2222-222222222222"
        correlation = "33333333-3333-3333-3333-333333333333"
        ctx = context_from_headers(
            {
                HEADER_PROJECT: project,
                HEADER_SESSION: session,
                HEADER_ACTOR: "agent:claude_code",
                HEADER_CORRELATION: correlation,
            }
        )
        assert ctx.project_id == UUID(project)
        assert ctx.session_id == UUID(session)
        assert ctx.actor_id == "agent:claude_code"
        assert ctx.correlation_id == UUID(correlation)

    def test_case_insensitive_headers(self) -> None:
        project = "11111111-1111-1111-1111-111111111111"
        ctx = context_from_headers(
            {
                "x-metaforge-project": project,
                "x-metaforge-actor": "user:fidel",
            }
        )
        assert ctx.project_id == UUID(project)
        assert ctx.actor_id == "user:fidel"

    def test_missing_headers_use_defaults(self) -> None:
        ctx = context_from_headers({})
        assert ctx.project_id is None
        assert ctx.actor_id == "system:unattributed"
        # session_id + correlation_id still auto-generated.
        assert isinstance(ctx.session_id, UUID)

    def test_empty_headers_dict(self) -> None:
        ctx = context_from_headers(None)
        assert ctx.actor_id == "system:unattributed"

    def test_invalid_uuid_falls_back_to_default(self) -> None:
        """An unparseable project header doesn't raise — drops to None."""
        ctx = context_from_headers({HEADER_PROJECT: "not-a-uuid"})
        assert ctx.project_id is None


# ---------------------------------------------------------------------------
# Header serialization (FORGE-76)
# ---------------------------------------------------------------------------


class TestHeaderSerialization:
    def test_ambient_sentinel_produces_no_headers(self) -> None:
        """current_context() with nothing ever installed is the exact
        _DEFAULT_CONTEXT singleton -- that sends nothing, so a truly
        unscoped call (nobody ever called set_context/with_context)
        behaves exactly as before this existed: no headers at all."""
        assert context_to_headers(current_context()) == {}

    def test_freshly_constructed_context_is_not_the_sentinel(self) -> None:
        """A bare McpCallContext() is a deliberately-installed context (its
        session_id/correlation_id are freshly generated, unlike the
        sentinel's fixed all-zero ids) -- it DOES produce headers, just
        without a project one."""
        headers = context_to_headers(McpCallContext())
        assert headers[HEADER_ACTOR] == "system:unattributed"
        assert HEADER_PROJECT not in headers

    def test_real_context_produces_all_headers_when_project_set(self) -> None:
        ctx = McpCallContext(
            project_id=UUID("11111111-1111-1111-1111-111111111111"),
            actor_id="agent:claude_code",
        )
        headers = context_to_headers(ctx)
        assert headers[HEADER_PROJECT] == str(ctx.project_id)
        assert headers[HEADER_SESSION] == str(ctx.session_id)
        assert headers[HEADER_ACTOR] == "agent:claude_code"
        assert headers[HEADER_CORRELATION] == str(ctx.correlation_id)

    def test_no_project_omits_project_header_only(self) -> None:
        """project_id=None is a real, meaningful 'no scope' signal (admin
        path) -- distinct from the sentinel case, which sends nothing."""
        ctx = McpCallContext(actor_id="agent:claude_code")
        headers = context_to_headers(ctx)
        assert HEADER_PROJECT not in headers
        assert headers[HEADER_ACTOR] == "agent:claude_code"

    def test_round_trip_through_from_headers(self) -> None:
        original = McpCallContext(
            project_id=UUID("11111111-1111-1111-1111-111111111111"),
            actor_id="user:fidel",
        )
        reconstructed = context_from_headers(context_to_headers(original))
        assert reconstructed.project_id == original.project_id
        assert reconstructed.session_id == original.session_id
        assert reconstructed.actor_id == original.actor_id
        assert reconstructed.correlation_id == original.correlation_id


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------


class TestEnvParsing:
    def test_full_env_set(self) -> None:
        project = "11111111-1111-1111-1111-111111111111"
        session = "22222222-2222-2222-2222-222222222222"
        correlation = "33333333-3333-3333-3333-333333333333"
        ctx = context_from_env(
            {
                ENV_PROJECT: project,
                ENV_SESSION: session,
                ENV_ACTOR: "agent:codex",
                ENV_CORRELATION: correlation,
            }
        )
        assert ctx.project_id == UUID(project)
        assert ctx.session_id == UUID(session)
        assert ctx.actor_id == "agent:codex"
        assert ctx.correlation_id == UUID(correlation)

    def test_empty_env_uses_defaults(self) -> None:
        ctx = context_from_env({})
        assert ctx.project_id is None
        assert ctx.actor_id == "system:unattributed"


# ---------------------------------------------------------------------------
# ContextVar plumbing
# ---------------------------------------------------------------------------


class TestContextVar:
    def test_default_current_context_is_sentinel(self) -> None:
        ctx = current_context()
        assert ctx.actor_id == "system:unattributed"

    def test_set_and_reset_via_token(self) -> None:
        custom = McpCallContext(actor_id="user:fidel")
        token = set_context(custom)
        try:
            assert current_context().actor_id == "user:fidel"
        finally:
            reset_context(token)
        # After reset, default sentinel is back.
        assert current_context().actor_id == "system:unattributed"

    def test_with_context_block(self) -> None:
        custom = McpCallContext(actor_id="agent:claude_code")
        with with_context(custom):
            assert current_context().actor_id == "agent:claude_code"
        # After block, default sentinel is back.
        assert current_context().actor_id == "system:unattributed"

    def test_with_context_nested(self) -> None:
        outer = McpCallContext(actor_id="user:outer")
        inner = McpCallContext(actor_id="user:inner")
        with with_context(outer):
            assert current_context().actor_id == "user:outer"
            with with_context(inner):
                assert current_context().actor_id == "user:inner"
            assert current_context().actor_id == "user:outer"
        assert current_context().actor_id == "system:unattributed"

    def test_with_context_resets_on_exception(self) -> None:
        custom = McpCallContext(actor_id="user:fidel")
        with pytest.raises(ValueError, match="boom"):
            with with_context(custom):
                assert current_context().actor_id == "user:fidel"
                raise ValueError("boom")
        # After exception, default sentinel still restored.
        assert current_context().actor_id == "system:unattributed"


# ---------------------------------------------------------------------------
# Async isolation — ContextVar is asyncio-aware.
# ---------------------------------------------------------------------------


class TestAsyncIsolation:
    async def test_concurrent_tasks_see_independent_contexts(self) -> None:
        """Two asyncio tasks installing different contexts don't trample
        each other — ContextVar copies on task creation.
        """
        results: list[str] = []
        ctx_a = McpCallContext(actor_id="user:alpha")
        ctx_b = McpCallContext(actor_id="user:beta")

        async def task(ctx: McpCallContext, label: str) -> None:
            with with_context(ctx):
                # Yield so the two tasks interleave.
                await asyncio.sleep(0)
                results.append(f"{label}={current_context().actor_id}")

        await asyncio.gather(task(ctx_a, "A"), task(ctx_b, "B"))
        assert "A=user:alpha" in results
        assert "B=user:beta" in results
