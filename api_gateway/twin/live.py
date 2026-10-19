"""The live view of an assembly being put together (FORGE-647).

``twin.place_part`` reports each part an agent places, moves or removes. This
hub keeps the placements per project and streams them to the viewer, which
composes the parts' cached GLB models at the given transforms. The viewer sees
a part appear within the time it takes the event to travel, with no STEP to
GLB conversion per step.

The hub holds the in-progress picture, not the record. The committed assembly
is still made by ``cadquery.create_assembly`` and ``twin.commit_geometry``; a
gateway restart drops the live picture and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["LiveAssemblyHub", "hub", "live_event_stream"]

#: Most events kept per project for a reconnecting client to replay.
REPLAY_LIMIT = 200

#: Most events held per subscriber before the oldest is dropped.
SUBSCRIBER_QUEUE_LIMIT = 256

_CLOSE = object()


class LiveAssemblyHub:
    """Per-project placements, a sequence number and a replay buffer."""

    def __init__(self) -> None:
        self._seq: dict[str, int] = defaultdict(int)
        self._state: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(dict)
        self._replay: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=REPLAY_LIMIT)
        )
        self._subscribers: dict[str, list[asyncio.Queue[object]]] = defaultdict(list)

    def publish(self, project_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Record one placement event and fan it out. Sync-callable."""
        self._seq[project_id] += 1
        stamped = {**event, "seq": self._seq[project_id], "at": time.time()}
        assembly = str(stamped.get("assembly") or "Assembly")
        part = stamped.get("part") or {}
        node_id = str(part.get("node_id") or "")
        parts = self._state[project_id].setdefault(assembly, {})
        kind = stamped.get("type")
        if kind == "assembly.part_removed":
            parts.pop(node_id, None)
        elif kind == "assembly.part_placed" and node_id:
            parts[node_id] = {
                "node_id": node_id,
                "name": part.get("name") or node_id,
                "position": stamped.get("position") or [0.0, 0.0, 0.0],
                "rotation": stamped.get("rotation") or [0.0, 0.0, 0.0],
                "seq": stamped["seq"],
            }
        elif kind == "assembly.cleared":
            parts.clear()
        self._replay[project_id].append(stamped)
        for queue in list(self._subscribers.get(project_id, ())):
            if queue.qsize() >= SUBSCRIBER_QUEUE_LIMIT:
                _drop_oldest(queue)
            queue.put_nowait(stamped)
        return stamped

    def snapshot(self, project_id: str) -> dict[str, Any]:
        """Every assembly in progress in the project, with its placed parts."""
        return {
            "project_id": project_id,
            "seq": self._seq.get(project_id, 0),
            "assemblies": [
                {"name": name, "parts": sorted(parts.values(), key=lambda p: p["seq"])}
                for name, parts in self._state.get(project_id, {}).items()
            ],
        }

    def replay_after(self, project_id: str, last_event_id: int) -> list[dict[str, Any]]:
        return [e for e in self._replay.get(project_id, ()) if e["seq"] > last_event_id]

    def subscribe(self, project_id: str) -> asyncio.Queue[object]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        self._subscribers[project_id].append(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue[object]) -> None:
        subs = self._subscribers.get(project_id)
        if subs and queue in subs:
            subs.remove(queue)
        if subs is not None and not subs:
            self._subscribers.pop(project_id, None)

    def close(self, project_id: str) -> None:
        for queue in list(self._subscribers.get(project_id, ())):
            queue.put_nowait(_CLOSE)


def _drop_oldest(queue: asyncio.Queue[object]) -> None:
    if not queue.empty():
        queue.get_nowait()


def _frame(event_name: str, seq: int, body: dict[str, Any]) -> str:
    return f"id: {seq}\nevent: {event_name}\ndata: {json.dumps(body)}\n\n"


async def live_event_stream(
    project_id: str,
    live_hub: LiveAssemblyHub,
    last_event_id: int | None = None,
) -> AsyncIterator[str]:
    """SSE frames for a project: the current picture, then each change.

    The first frame is ``live.snapshot``, the whole in-progress picture, so a
    viewer that opens late or reconnects after a gap is correct without
    replaying anything. Frames after it are ``live.event``, one per change.
    ``last_event_id`` skips changes the snapshot already contains.
    """
    queue = live_hub.subscribe(project_id)
    try:
        snapshot = live_hub.snapshot(project_id)
        sent = int(snapshot["seq"])
        yield _frame("live.snapshot", sent, snapshot)
        while True:
            item = await queue.get()
            if item is _CLOSE:
                return
            assert isinstance(item, dict)
            if item["seq"] <= sent:
                continue
            sent = item["seq"]
            yield _frame("live.event", sent, item)
    finally:
        live_hub.unsubscribe(project_id, queue)
        logger.info("live_stream_closed", project_id=project_id)


hub = LiveAssemblyHub()
