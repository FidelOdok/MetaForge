"""Custom span helpers and decorators for domain-specific instrumentation (MET-106)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any

# Try to import OTel, fall back to no-ops
try:
    from opentelemetry import trace

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


class NoOpSpan:
    """No-op span when OTel is not available."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class NoOpTracer:
    """No-op tracer when OTel is not available."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> NoOpSpan:
        return NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> NoOpSpan:
        return NoOpSpan()


def get_tracer(name: str = "metaforge") -> Any:
    """Get OTel tracer or no-op."""
    if HAS_OTEL:
        return trace.get_tracer(name)
    return NoOpTracer()


# Span catalog - 10 span types with their expected attributes
SPAN_CATALOG: dict[str, list[str]] = {
    "agent.execute": [
        "agent.code",
        "session.id",
        "agent.llm_tokens_total",
        "agent.llm_cost_usd",
    ],
    "skill.execute": [
        "skill.name",
        "skill.domain",
        "skill.input_size",
    ],
    "mcp.tool_call": [
        "tool.name",
        "tool.adapter",
        "tool.timeout_ms",
    ],
    "neo4j.query": [
        "db.statement",
        "db.operation",
        "neo4j.result_count",
    ],
    "pgvector.search": [
        "pgvector.query_embedding_dim",
        "pgvector.top_k",
        "pgvector.similarity_threshold",
    ],
    "kafka.produce": [
        "messaging.destination",
        "messaging.message_id",
    ],
    "kafka.consume": [
        "messaging.destination",
        "messaging.consumer_group",
    ],
    "llm.completion": [
        "llm.provider",
        "llm.model",
        "llm.tokens.prompt",
        "llm.tokens.completion",
        "llm.cost_usd",
    ],
    "constraint.evaluate": [
        "constraint.rule_id",
        "constraint.domain",
        "constraint.result",
    ],
    "opa.decision": [
        "opa.policy",
        "opa.result",
        "opa.input_size",
    ],
}


def traced(span_name: str, attributes: dict[str, Any] | None = None) -> Callable:
    """Decorator to wrap a function in a traced span."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    raise

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
