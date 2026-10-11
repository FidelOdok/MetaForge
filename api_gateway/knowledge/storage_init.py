"""Set up the knowledge-base object storage when the gateway starts (FORGE-594).

``MinIOKBStorage.initialize`` creates the KB bucket, turns versioning on so
datasheet history is never overwritten in place (MET-476), and writes the
prefix markers. Nothing called it: the bucket was created on the first blob
write by a path that does not turn versioning on, so ``metaforge-kb`` ran
with versioning off and nobody was told.

The gateway now runs it in the background at start-up. The object store can
start after the gateway, so a failure is retried for a while before it is
reported. A final failure is an error log and a ``failed`` count on
``metaforge_kb_storage_init_total``, which the ``KBStorageInitFailed`` alert
watches. It never stops the gateway from serving: everything except KB
history keeps working without it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.knowledge.storage_init")

__all__ = ["ensure_kb_storage"]

#: Attempts before giving up, and the pause between them. About five
#: minutes in all, long enough for an object store started alongside.
DEFAULT_ATTEMPTS = 20
DEFAULT_DELAY_SECONDS = 15.0


def _record(outcome: str) -> None:
    try:
        from observability.metrics import collector_for

        collector_for("metaforge-gateway").record_kb_storage_init(outcome)
    except Exception as exc:  # noqa: BLE001 - a metric must never break start-up
        logger.warning("kb_storage_init_metric_failed", error=str(exc))


def _default_storage(env: Mapping[str, str]) -> Any:
    from digital_twin.storage import MinIOKBStorage, MinIOSettings

    return MinIOKBStorage(MinIOSettings.from_env(env))


async def ensure_kb_storage(
    env: Mapping[str, str] | None = None,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    storage_factory: Callable[[Mapping[str, str]], Any] = _default_storage,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Initialise the KB bucket; returns ``ready``, ``failed`` or ``skipped``."""
    src = os.environ if env is None else env
    if not str(src.get("MINIO_ENDPOINT") or "").strip():
        # No object store configured (a local dev gateway): nothing to set up.
        logger.info("kb_storage_init_skipped", reason="MINIO_ENDPOINT not set")
        _record("skipped")
        return "skipped"
    with tracer.start_as_current_span("kb_storage.init") as span:
        last_error = ""
        for attempt in range(1, max(attempts, 1) + 1):
            try:
                storage = storage_factory(src)
                await storage.initialize()
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last_error = str(exc)
                logger.warning("kb_storage_init_attempt_failed", attempt=attempt, error=last_error)
                if attempt < attempts:
                    await sleep(delay_seconds)
                continue
            span.set_attribute("kb_storage.attempts", attempt)
            logger.info("kb_storage_ready", bucket=getattr(storage, "bucket", ""), attempts=attempt)
            _record("ready")
            return "ready"
        span.set_attribute("kb_storage.attempts", attempts)
        span.set_attribute("error", True)
        logger.error(
            "kb_storage_init_failed",
            attempts=attempts,
            error=last_error,
            consequence=(
                "the KB bucket may lack versioning, so datasheet history can be "
                "overwritten in place; run scripts/storage/provision_minio.py once fixed"
            ),
        )
        _record("failed")
        return "failed"
