"""What producers call to report an operation's progress (FORGE-785).

``publish(project_id, operation_id, type, state, **fields)`` stores the event
in the durable log, folds it into the operation's snapshot and streams it to
the project's subscribers. It assigns ``sequence`` per operation (the next one
after the operation's last, read from the store in the append's own
transaction, so it survives a restart) unless the producer gives one.

Publishing is best effort and **never raises into the caller**: a run
transition or a tool job must not fail because its status line could not be
written. A refusal (out of order, or after the operation ended) and an error
are logged and counted, and ``None`` is returned.

The process-wide publisher is in-memory until the gateway's start-up calls
:func:`init_operations` with a file-backed store.

FORGE-788 (scheduling) adds two rules, applied here so every producer gets
them, whether it runs in the gateway or posts from the sidecar:

* **Supersede.** When a new job operation names a ``target_key`` (an item
  key, for example), older job operations on the same target that are still
  open are ended ``superseded``, and the job itself is stopped where it runs.
  A late event from a superseded job is then refused by the terminal guard,
  so its result is never reported as the current one.
* **Stale results.** A ``completed`` event that carries a ``target_key`` and
  the ``base_revision`` it worked from is checked against the target's head
  through the installed head resolver (:func:`set_head_resolver`). When the
  head has moved since, the event is stored as ``stale`` with a message that
  says so: the newer revision stays the head and the result is never adopted.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from api_gateway.operations.hub import OperationHub
from api_gateway.operations.schemas import (
    OperationEvent,
    OperationEventIn,
    OperationEventType,
    OperationKind,
    OperationSnapshot,
    OperationState,
    Severity,
    is_terminal,
    kind_of,
)
from api_gateway.operations.store import AppendResult, OperationStore
from observability.metrics import MetricsCollector
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.publisher")

__all__ = [
    "OperationPublisher",
    "get_operation_publisher",
    "init_operations",
    "publish",
    "publish_payload",
    "publish_payload_screened",
    "set_head_resolver",
    "set_job_cancel_relay",
    "set_metrics_collector",
    "terminal_event_id",
]

_collector: MetricsCollector | None = None

#: ``(project_id, target_key) -> the target's head revision``, or ``None``
#: when it has none or is unknown. Installed by the gateway at start-up.
HeadResolver = Callable[[str, str], Awaitable[str | None]]
_head_resolver: HeadResolver | None = None

#: Stops a job that another process (the MCP sidecar) runs; takes the job id.
JobCancelRelay = Callable[[str], Awaitable[Any]]
_job_cancel_relay: JobCancelRelay | None = None

SUPERSEDED_MESSAGE = "Replaced by a newer request on the same target"

#: A job's ending has a fixed sequence (``tool_registry.jobs``), so an ending
#: reported here and the same ending reported by its runner are one event.
JOB_TERMINAL_SEQUENCE = 90


def set_metrics_collector(collector: MetricsCollector | None) -> None:
    """Wire the gateway's collector in (``server.py`` at start-up)."""
    global _collector  # noqa: PLW0603
    _collector = collector


def _metrics() -> MetricsCollector:
    return _collector if _collector is not None else MetricsCollector()


def set_head_resolver(resolver: HeadResolver | None) -> None:
    """Install (or remove) what reads a target's current head (FORGE-788)."""
    global _head_resolver  # noqa: PLW0603
    _head_resolver = resolver


def head_resolver() -> HeadResolver | None:
    """The installed head resolver, if any."""
    return _head_resolver


def set_job_cancel_relay(relay: JobCancelRelay | None) -> None:
    """Install (or remove) the relay that stops a job another process runs."""
    global _job_cancel_relay  # noqa: PLW0603
    _job_cancel_relay = relay


def job_cancel_relay() -> JobCancelRelay | None:
    """The installed sidecar relay, if any."""
    return _job_cancel_relay


def terminal_event_id(operation_id: str, status: str) -> str:
    """The deterministic ``event_id`` of an operation's ending.

    A job's runner (``tool_registry.jobs``) derives its events' ids the same
    way, so when the gateway and the runner both report one ending, the second
    report is a duplicate rather than a second event.
    """
    kind, _, native = operation_id.partition(":")
    if kind == "job":
        name = f"metaforge:job:{native}:{status}"
    else:
        name = f"metaforge:{operation_id}:{status}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


class OperationPublisher:
    """Stores events, folds snapshots and fans them out. Never raises."""

    def __init__(
        self,
        store: OperationStore,
        hub: OperationHub | None = None,
        *,
        metrics: Callable[[], MetricsCollector] = _metrics,
    ) -> None:
        self.store = store
        self.hub = hub if hub is not None else OperationHub(store, metrics=metrics)
        self._metrics = metrics
        self._relays: set[asyncio.Task[None]] = set()
        try:
            for kind, count in store.count_active().items():
                metrics().record_operation_active(kind, count)
        except Exception as exc:  # noqa: BLE001
            logger.info("operation_active_gauge_unavailable", error=str(exc))

    def ingest(self, project_id: str, incoming: OperationEventIn) -> AppendResult:
        """Append one event; raises only what the store raises (the route's 5xx)."""
        kind = str(kind_of(incoming.operation_id) or "unknown")
        with tracer.start_as_current_span("operations.publish") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("operation_id", incoming.operation_id)
            span.set_attribute("event_type", str(incoming.type))
            previous = self.store.get(project_id, incoming.operation_id)
            try:
                result = self.store.append(project_id, incoming)
            except Exception as exc:
                span.record_exception(exc)
                self._metrics().record_operation_publish_failure(kind, "error")
                raise
            span.set_attribute("outcome", result.status)
        if result.status == "stored" and result.event is not None:
            self._after_store(result.event, previous_state=previous.state if previous else None)
            if previous is None and result.event.target_key:
                self._supersede_older(project_id, result.event)
        elif result.status == "refused":
            reason = "after_terminal" if "already" in (result.reason or "") else "out_of_order"
            self._metrics().record_operation_publish_failure(kind, reason)
            logger.info(
                "operation_event_refused",
                project_id=project_id,
                operation_id=incoming.operation_id,
                type=str(incoming.type),
                sequence=incoming.sequence,
                reason=result.reason,
            )
        return result

    # ── FORGE-788: supersede and stale results ──────────────────────────

    def _supersede_older(self, project_id: str, newer: OperationEvent) -> None:
        """End the older open jobs on ``newer``'s target. Never raises.

        Only jobs: a job's work is one call whose result the newer one
        replaces, so stopping it loses nothing. A run has gates and drafts of
        its own and is never ended because something else started.
        """
        if newer.kind is not OperationKind.JOB or not newer.target_key:
            return
        try:
            older = self.store.active_for_target(
                project_id, newer.target_key, exclude=newer.operation_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("operation_supersede_lookup_failed", error=str(exc))
            return
        for snapshot in older:
            if snapshot.kind is OperationKind.JOB:
                self.supersede(project_id, snapshot, by=newer.operation_id)

    def supersede(self, project_id: str, snapshot: OperationSnapshot, *, by: str) -> None:
        """Mark one open job ``superseded`` and stop it where it runs. Never raises."""
        with tracer.start_as_current_span("operations.supersede") as span:
            span.set_attribute("operation_id", snapshot.operation_id)
            span.set_attribute("superseded_by", by)
            self.publish(
                project_id,
                snapshot.operation_id,
                OperationEventType.SUPERSEDED.value,
                OperationState.SUPERSEDED.value,
                event_id=terminal_event_id(snapshot.operation_id, "superseded"),
                sequence=max(JOB_TERMINAL_SEQUENCE, snapshot.last_sequence + 1),
                title=snapshot.title,
                domain=snapshot.domain.value,
                target_key=snapshot.target_key,
                message=SUPERSEDED_MESSAGE,
            )
        logger.info(
            "operation_superseded",
            project_id=project_id,
            operation_id=snapshot.operation_id,
            by=by,
        )
        self._stop_job(snapshot.operation_id.partition(":")[2], by)

    def _stop_job(self, job_id: str, by: str) -> None:
        try:
            from tool_registry.jobs import active_job_runner

            runner = active_job_runner()
            if runner is not None and runner.owns(job_id):
                runner.supersede(job_id, by=by)
                return
        except Exception as exc:  # noqa: BLE001
            logger.info("operation_supersede_stop_failed", job_id=job_id, error=str(exc))
            return
        relay = _job_cancel_relay
        if relay is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _relay() -> None:
            try:
                await relay(job_id)
            except Exception as exc:  # noqa: BLE001 - best effort; the event already ended it
                logger.info("operation_supersede_relay_failed", job_id=job_id, error=str(exc))

        task = loop.create_task(_relay())
        self._relays.add(task)
        task.add_done_callback(self._relays.discard)

    async def screen_result(self, project_id: str, incoming: OperationEventIn) -> OperationEventIn:
        """A ``completed`` result on a target whose head has moved becomes ``stale``.

        Only an event naming both ``target_key`` and ``base_revision`` is
        checked, and only with a head resolver installed. A resolver that
        fails lets the event through unchanged: this check reports, while the
        head itself refuses a pinned write that has gone stale.
        """
        resolver = _head_resolver
        if (
            incoming.type is not OperationEventType.COMPLETED
            or not incoming.target_key
            or not incoming.base_revision
            or resolver is None
        ):
            return incoming
        with tracer.start_as_current_span("operations.screen_result") as span:
            span.set_attribute("operation_id", incoming.operation_id)
            span.set_attribute("target_key", incoming.target_key)
            try:
                head = await resolver(project_id, incoming.target_key)
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                logger.info(
                    "operation_head_unavailable",
                    project_id=project_id,
                    target_key=incoming.target_key,
                    error=str(exc),
                )
                return incoming
            span.set_attribute("head_revision", str(head))
        if head is None or head == incoming.base_revision:
            return incoming
        logger.info(
            "operation_result_stale",
            project_id=project_id,
            operation_id=incoming.operation_id,
            target_key=incoming.target_key,
            base_revision=incoming.base_revision,
            head_revision=head,
        )
        return incoming.model_copy(
            update={
                "type": OperationEventType.STALE,
                "state": OperationState.STALE,
                "severity": Severity.WARNING,
                "message": stale_message(incoming.target_key, incoming.base_revision, head),
                "target_revision": head,
                "action_refs": [],
            }
        )

    async def ingest_screened(self, project_id: str, incoming: OperationEventIn) -> AppendResult:
        """:meth:`ingest` after :meth:`screen_result` (the async producers' path)."""
        return self.ingest(project_id, await self.screen_result(project_id, incoming))

    def _after_store(self, event: OperationEvent, *, previous_state: Any) -> None:
        kind = str(event.kind)
        lag = 0.0
        if event.occurred_at is not None:
            lag = time.time() - event.occurred_at.timestamp()
        metrics = self._metrics()
        metrics.record_operation_event(kind, str(event.type), lag)
        if event.type is OperationEventType.SUPERSEDED:
            metrics.record_operation_scheduling(kind, "superseded")
        elif event.type is OperationEventType.STALE:
            metrics.record_operation_scheduling(kind, "stale_rejected")
        was_active = previous_state is not None and not is_terminal(previous_state)
        now_active = not is_terminal(event.state)
        if now_active and not was_active:
            metrics.record_operation_active(kind, 1)
        elif was_active and not now_active:
            metrics.record_operation_active(kind, -1)
        log = logger.warning if str(event.severity) == "error" else logger.info
        log(
            "operation_event_published",
            project_id=event.project_id,
            project_seq=event.project_seq,
            operation_id=event.operation_id,
            sequence=event.sequence,
            type=str(event.type),
            state=str(event.state),
            stage_id=event.stage_id,
        )
        self.hub.publish(event)

    def publish(
        self,
        project_id: str,
        operation_id: str,
        type: str,  # noqa: A002 - the contract's field name
        state: str,
        **fields: Any,
    ) -> OperationEvent | None:
        """Store and stream one event. ``None`` when refused or on any error."""
        try:
            incoming = OperationEventIn(
                operation_id=operation_id,
                type=type,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                **fields,
            )
            result = self.ingest(project_id, incoming)
        except Exception as exc:  # noqa: BLE001 - publishing never fails the caller
            kind = str(kind_of(operation_id) or "unknown")
            try:
                self._metrics().record_operation_publish_failure(kind, "error")
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "operation_event_publish_failed",
                project_id=project_id,
                operation_id=operation_id,
                type=type,
                error=str(exc),
            )
            return None
        return result.event if result.status != "refused" else None

    def publish_payload(self, project_id: str, payload: dict[str, Any]) -> OperationEvent | None:
        """:meth:`publish` from a dict (the cross-process seam's shape)."""
        try:
            fields = dict(payload)
            fields.pop("project_id", None)
            operation_id = str(fields.pop("operation_id"))
            event_type = str(fields.pop("type"))
            state = str(fields.pop("state"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("operation_event_payload_invalid", project_id=project_id, error=str(exc))
            return None
        return self.publish(project_id, operation_id, event_type, state, **fields)

    async def publish_payload_screened(
        self, project_id: str, payload: dict[str, Any]
    ) -> OperationEvent | None:
        """:meth:`publish_payload` with the stale-result check. Never raises."""
        try:
            fields = dict(payload)
            fields.pop("project_id", None)
            incoming = OperationEventIn(**fields)
            result = await self.ingest_screened(project_id, incoming)
        except Exception as exc:  # noqa: BLE001 - publishing never fails the caller
            logger.warning(
                "operation_event_publish_failed",
                project_id=project_id,
                operation_id=payload.get("operation_id"),
                error=str(exc),
            )
            return None
        return result.event if result.status != "refused" else None


def stale_message(target_key: str, base_revision: str, head: str) -> str:
    """The server-authored line a stale result carries."""
    return (
        f"{target_key} moved on to revision {head} while this worked from revision "
        f"{base_revision}. The newer revision was kept; this result was not applied."
    )[:500]


_publisher: OperationPublisher | None = None


def get_operation_publisher() -> OperationPublisher:
    """The process publisher; in-memory until :func:`init_operations` runs."""
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = OperationPublisher(OperationStore(":memory:"))
    return _publisher


def init_operations(store: OperationStore | None) -> OperationPublisher:
    """Install a publisher over ``store`` (``None``: a fresh in-memory one)."""
    global _publisher  # noqa: PLW0603
    _publisher = OperationPublisher(store if store is not None else OperationStore(":memory:"))
    logger.info("operation_store_opened", path=_publisher.store.path)
    return _publisher


def publish(
    project_id: str,
    operation_id: str,
    type: str,  # noqa: A002 - the contract's field name
    state: str,
    **fields: Any,
) -> OperationEvent | None:
    """Best-effort publish through the process publisher. Never raises."""
    try:
        publisher = get_operation_publisher()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation_store_unavailable", error=str(exc))
        return None
    return publisher.publish(project_id, operation_id, type, state, **fields)


def publish_payload(project_id: str, payload: dict[str, Any]) -> OperationEvent | None:
    """Best-effort publish of a dict event. Never raises."""
    try:
        publisher = get_operation_publisher()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation_store_unavailable", error=str(exc))
        return None
    return publisher.publish_payload(project_id, payload)


async def publish_payload_screened(
    project_id: str, payload: dict[str, Any]
) -> OperationEvent | None:
    """Best-effort publish of a dict event, with the stale-result check. Never raises."""
    try:
        publisher = get_operation_publisher()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation_store_unavailable", error=str(exc))
        return None
    return await publisher.publish_payload_screened(project_id, payload)
