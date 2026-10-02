"""Which provider actually answered, and when the primary did not (FORGE-468).

Two things the provider pipeline used to keep to itself:

- **Fallback.** When the primary provider for a role failed and another one
  answered, the only trace was an INFO ``provider_complete_ok`` line naming
  the provider that served it. A primary that 400'd on every call for weeks
  looked exactly like a healthy fallback chain. :func:`record_fallback` makes
  it an alarm: a WARNING log, a counter, and the last occurrence kept for the
  harness status route.
- **Provenance.** A caller of ``run_chat_turn`` got a reply string and no
  way to tell which model wrote it. :func:`capture_served` lets a caller
  collect every provider/model the pipeline used inside a block.

Process-local and best-effort: neither may ever fail a model call.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.metrics import MetricsCollector

logger = structlog.get_logger(__name__)

__all__ = [
    "FallbackEvent",
    "ServedBy",
    "ServedCapture",
    "capture_served",
    "last_fallback",
    "note_served",
    "record_fallback",
    "reset_fallback_state",
]


@dataclass(frozen=True)
class ServedBy:
    """The provider and model that produced one model response."""

    provider: str
    model: str
    role: str
    #: ``"<provider>:<model>"`` of the primary when this response came from a
    #: fallback; ``None`` when the primary answered.
    fell_back_from: str | None = None


@dataclass(frozen=True)
class FallbackEvent:
    """One time a role was served by something other than its primary."""

    role: str
    primary: str
    primary_model: str
    fallback: str
    fallback_model: str
    #: Why the primary did not answer (its last error, or why it was dropped).
    error: str
    #: ``"call_failed"`` (primary was tried and failed), ``"model_mismatch"``
    #: (primary was dropped at config time; see
    #: ``registry.model_family_mismatch``) or ``"capability_unsupported"``
    #: (primary's family declined by design, e.g. no event streaming).
    reason: str = "call_failed"
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServedCapture:
    """What :func:`capture_served` collected inside its block."""

    served: list[ServedBy] = field(default_factory=list)
    #: ``"<provider>:<model>"`` of a primary dropped before any call because
    #: the pair cannot work. The pipeline then treats the next candidate as
    #: its primary, so this is the only place that fact survives.
    dropped_primary: str | None = None

    def last(self) -> ServedBy | None:
        """The final response's provenance, with any dropped primary folded in."""
        if not self.served:
            return None
        final = self.served[-1]
        if final.fell_back_from is None and self.dropped_primary is not None:
            return replace(final, fell_back_from=self.dropped_primary)
        return final


_lock = threading.Lock()
_last: FallbackEvent | None = None
_count = 0

_served: ContextVar[ServedCapture | None] = ContextVar("harness_served_by", default=None)


def record_fallback(
    *,
    role: str,
    primary: str,
    primary_model: str,
    fallback: str,
    fallback_model: str,
    error: str,
    reason: str = "call_failed",
    metrics: MetricsCollector | None = None,
) -> FallbackEvent:
    """Raise the alarm that ``role`` was not served by its primary."""
    global _last, _count  # noqa: PLW0603
    event = FallbackEvent(
        role=role,
        primary=primary,
        primary_model=primary_model,
        fallback=fallback,
        fallback_model=fallback_model,
        error=error[:500],
        reason=reason,
    )
    alarming = reason != "capability_unsupported"
    if alarming:
        # Kept out of the status otherwise: a routine capability decline
        # must not overwrite the last real failure.
        with _lock:
            _last = event
            _count += 1
    sink = _served.get()
    if sink is not None and reason == "model_mismatch" and sink.dropped_primary is None:
        sink.dropped_primary = f"{primary}:{primary_model}"
    # A by-design capability decline (e.g. Codex has no event-streaming
    # adapter) is recorded but is not a fault, so it does not warn.
    log = logger.warning if alarming else logger.info
    log(
        "harness_provider_fallback",
        role=role,
        primary=primary,
        primary_model=primary_model,
        fallback=fallback,
        fallback_model=fallback_model,
        reason=reason,
        primary_error=event.error,
    )
    if metrics is not None:
        try:
            metrics.record_harness_provider_fallback(primary, fallback, role, reason)
        except Exception:  # noqa: BLE001 - metrics must never break a provider call
            pass
    return event


def last_fallback() -> tuple[FallbackEvent | None, int]:
    """The most recent fallback in this process and how many there have been."""
    with _lock:
        return _last, _count


def reset_fallback_state() -> None:
    """Forget recorded fallbacks (tests)."""
    global _last, _count  # noqa: PLW0603
    with _lock:
        _last = None
        _count = 0


@contextmanager
def capture_served() -> Iterator[ServedCapture]:
    """Collect every :class:`ServedBy` the pipeline records inside the block.

    The capture object is shared through a context variable, so calls made in
    child tasks (which copy the context) still land in it.
    """
    sink = ServedCapture()
    token = _served.set(sink)
    try:
        yield sink
    finally:
        _served.reset(token)


def note_served(served: ServedBy) -> None:
    """Record that ``served`` produced a response (no-op outside a capture)."""
    sink = _served.get()
    if sink is not None:
        sink.served.append(served)
