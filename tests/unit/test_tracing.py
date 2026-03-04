"""Tests for observability.tracing (MET-106): custom span helpers and decorators."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from observability.tracing import (
    SPAN_CATALOG,
    NoOpSpan,
    NoOpTracer,
    get_tracer,
    traced,
)

# ── NoOpSpan tests ─────────────────────────────────────────────────────


class TestNoOpSpan:
    """NoOpSpan must never raise, regardless of how it is used."""

    def test_set_attribute_does_not_raise(self) -> None:
        span = NoOpSpan()
        span.set_attribute("key", "value")

    def test_set_status_does_not_raise(self) -> None:
        span = NoOpSpan()
        span.set_status("OK")

    def test_record_exception_does_not_raise(self) -> None:
        span = NoOpSpan()
        span.record_exception(RuntimeError("boom"))

    def test_end_does_not_raise(self) -> None:
        span = NoOpSpan()
        span.end()

    def test_context_manager_enter_returns_self(self) -> None:
        span = NoOpSpan()
        with span as s:
            assert s is span

    def test_context_manager_exit_does_not_raise(self) -> None:
        span = NoOpSpan()
        with span:
            pass  # should not raise


# ── NoOpTracer tests ───────────────────────────────────────────────────


class TestNoOpTracer:
    """NoOpTracer must produce NoOpSpan instances."""

    def test_start_as_current_span_returns_noop_span(self) -> None:
        tracer = NoOpTracer()
        span = tracer.start_as_current_span("test.span")
        assert isinstance(span, NoOpSpan)

    def test_start_span_returns_noop_span(self) -> None:
        tracer = NoOpTracer()
        span = tracer.start_span("test.span")
        assert isinstance(span, NoOpSpan)


# ── get_tracer tests ───────────────────────────────────────────────────


class TestGetTracer:
    """get_tracer should fall back to NoOpTracer when OTel is unavailable."""

    def test_returns_noop_tracer_without_otel(self) -> None:
        with patch("observability.tracing.HAS_OTEL", False):
            tracer = get_tracer()
            assert isinstance(tracer, NoOpTracer)

    def test_returns_noop_tracer_with_custom_name(self) -> None:
        with patch("observability.tracing.HAS_OTEL", False):
            tracer = get_tracer("custom-service")
            assert isinstance(tracer, NoOpTracer)


# ── SPAN_CATALOG tests ────────────────────────────────────────────────


class TestSpanCatalog:
    """SPAN_CATALOG must have exactly 10 entries, each with an attributes list."""

    def test_catalog_has_10_entries(self) -> None:
        assert len(SPAN_CATALOG) == 10

    def test_each_entry_has_attributes_list(self) -> None:
        for span_name, attrs in SPAN_CATALOG.items():
            assert isinstance(attrs, list), f"{span_name} attributes should be a list"
            assert len(attrs) > 0, f"{span_name} should have at least one attribute"

    def test_all_span_names_are_dotted(self) -> None:
        for span_name in SPAN_CATALOG:
            assert "." in span_name, f"Span name '{span_name}' should be dot-separated"

    def test_expected_span_names_present(self) -> None:
        expected = {
            "agent.execute",
            "skill.execute",
            "mcp.tool_call",
            "neo4j.query",
            "pgvector.search",
            "kafka.produce",
            "kafka.consume",
            "llm.completion",
            "constraint.evaluate",
            "opa.decision",
        }
        assert set(SPAN_CATALOG.keys()) == expected

    def test_all_attribute_names_are_strings(self) -> None:
        for span_name, attrs in SPAN_CATALOG.items():
            for attr in attrs:
                assert isinstance(attr, str), (
                    f"Attribute in {span_name} should be a string, got {type(attr)}"
                )


# ── @traced decorator tests ───────────────────────────────────────────


class TestTracedDecorator:
    """@traced must wrap both sync and async functions transparently."""

    def test_sync_function_returns_value(self) -> None:
        @traced("test.sync")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    async def test_async_function_returns_value(self) -> None:
        @traced("test.async")
        async def add(a: int, b: int) -> int:
            return a + b

        assert await add(2, 3) == 5

    def test_sync_function_with_attributes(self) -> None:
        @traced("test.sync.attr", attributes={"skill.name": "validate_stress"})
        def greet(name: str) -> str:
            return f"hello {name}"

        assert greet("world") == "hello world"

    async def test_async_function_with_attributes(self) -> None:
        @traced("test.async.attr", attributes={"agent.code": "MECH"})
        async def greet(name: str) -> str:
            return f"hello {name}"

        assert await greet("world") == "hello world"

    def test_sync_exception_propagates(self) -> None:
        @traced("test.sync.err")
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            boom()

    async def test_async_exception_propagates(self) -> None:
        @traced("test.async.err")
        async def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            await boom()

    def test_preserves_function_name(self) -> None:
        @traced("test.meta")
        def my_function() -> None:
            pass

        assert my_function.__name__ == "my_function"

    async def test_preserves_async_function_name(self) -> None:
        @traced("test.meta.async")
        async def my_async_function() -> None:
            pass

        assert my_async_function.__name__ == "my_async_function"

    def test_traced_with_no_otel_is_noop(self) -> None:
        """Ensure @traced works when OTel is missing (no-op path)."""
        with patch("observability.tracing.HAS_OTEL", False):

            @traced("test.noop")
            def identity(x: int) -> int:
                return x

            assert identity(42) == 42

    async def test_traced_async_with_no_otel_is_noop(self) -> None:
        """Ensure @traced works on async when OTel is missing (no-op path)."""
        with patch("observability.tracing.HAS_OTEL", False):

            @traced("test.noop.async")
            async def identity(x: int) -> int:
                return x

            assert await identity(42) == 42
