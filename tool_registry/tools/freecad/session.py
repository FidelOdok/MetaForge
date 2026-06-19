"""Stateful headless FreeCAD session store (MET-528).

PartDesign and Assembly authoring is inherently *stateful*: you create a Body,
add a Sketch on it, pad that sketch, then joint two parts — each call references
objects created by earlier calls. The stateless file-in/file-out pattern used by
the CadQuery adapter cannot express that. This store holds a live FreeCAD
document per ``session_id`` and a registry of the objects created in it, so MCP
tools can address prior objects by a stable ``obj_id`` across calls.

**Single-worker affinity.** A live FreeCAD document lives in one process; FreeCAD
is not thread-safe and documents cannot be shared across workers. The adapter is
a single long-lived process (the stdio container, or one in-process server), so a
session is pinned to that worker. Any future multi-worker HTTP deployment must
route a ``session_id`` back to the worker that owns it (sticky routing) — there
is no cross-process document sharing.

The store is **FreeCAD-agnostic and pure**: document creation/teardown are
injected callables (``doc_factory`` / ``doc_closer``). The defaults use the real
FreeCAD API when bindings are present and raise :class:`FreecadNotAvailableError`
otherwise — so the store (lifecycle, object registry, TTL eviction) is fully
unit-testable without FreeCAD installed by injecting fakes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import structlog

from observability.tracing import get_tracer
from tool_registry.tools.freecad.operations import FreecadNotAvailableError

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.freecad.session")

DEFAULT_TTL_SECONDS = 1800  # 30 min idle → evict
DEFAULT_MAX_SESSIONS = 32


class SessionNotFoundError(KeyError):
    """Raised when a session_id is unknown or has been evicted/closed."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.session_id = session_id

    def __str__(self) -> str:
        return (
            f"FreeCAD session {self.session_id!r} not found "
            "(unknown, closed, or evicted after idle TTL). Open a new session."
        )


class ObjectNotFoundError(KeyError):
    """Raised when an obj_id is not registered in a session."""

    def __init__(self, session_id: str, obj_id: str) -> None:
        super().__init__(obj_id)
        self.session_id = session_id
        self.obj_id = obj_id

    def __str__(self) -> str:
        return f"object {self.obj_id!r} not found in FreeCAD session {self.session_id!r}"


@dataclass
class ObjectEntry:
    """A single object authored in a session, addressable across MCP calls."""

    obj_id: str
    kind: str  # body | sketch | primitive | feature | assembly | part | joint | varset
    name: str
    obj: Any  # the live FreeCAD object — opaque to the store
    order: int
    metadata: dict[str, Any] = field(default_factory=dict)  # e.g. joint params (MET-530)


@dataclass
class Session:
    """A live FreeCAD document plus its object registry."""

    session_id: str
    name: str
    document: Any  # live FreeCAD document — opaque to the store
    created_at: float
    last_access: float
    objects: dict[str, ObjectEntry] = field(default_factory=dict)
    _counter: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "object_count": len(self.objects),
            "objects": [
                {
                    "obj_id": e.obj_id,
                    "kind": e.kind,
                    "name": e.name,
                    "order": e.order,
                    **({"metadata": e.metadata} if e.metadata else {}),
                }
                for e in sorted(self.objects.values(), key=lambda e: e.order)
            ],
        }

    def joints(self) -> list[dict[str, Any]]:
        """Return assembly joints in the kinematics ``Joint.from_dict`` shape
        (so the live solver / dashboard can consume them directly)."""
        return [
            e.metadata
            for e in sorted(self.objects.values(), key=lambda e: e.order)
            if e.kind == "joint" and e.metadata
        ]


def _default_doc_factory(name: str) -> Any:
    """Create a real FreeCAD document. Raises if bindings are unavailable."""
    from tool_registry.tools.freecad import operations as _ops

    if not _ops.HAS_FREECAD:
        raise FreecadNotAvailableError
    return _ops.FreeCAD.newDocument(name or "MetaForge")


def _default_doc_closer(document: Any) -> None:
    """Close a real FreeCAD document. Best-effort; never raises."""
    from tool_registry.tools.freecad import operations as _ops

    if not _ops.HAS_FREECAD or document is None:
        return
    try:
        _ops.FreeCAD.closeDocument(document.Name)
    except Exception as exc:  # noqa: BLE001 — teardown is best-effort
        logger.warning("freecad_doc_close_failed", error=str(exc))


class FreecadSessionStore:
    """In-process registry of live FreeCAD documents keyed by session_id.

    Thread-safe for registry mutations (a single ``threading.Lock`` guards the
    session dict and per-session counters). The FreeCAD operations themselves
    still require single-worker affinity (see module docstring).
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        doc_factory: Callable[[str], Any] | None = None,
        doc_closer: Callable[[Any], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._doc_factory = doc_factory or _default_doc_factory
        self._doc_closer = doc_closer or _default_doc_closer
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # ---- lifecycle ----------------------------------------------------

    def open_session(self, name: str = "") -> str:
        """Create a new live document and return its session_id."""
        with tracer.start_as_current_span("freecad.session.open") as span:
            document = self._doc_factory(name)
            now = self._clock()
            with self._lock:
                self._evict_locked(now)
                self._enforce_capacity_locked()
                session_id = self._id_factory()
                self._sessions[session_id] = Session(
                    session_id=session_id,
                    name=name or session_id,
                    document=document,
                    created_at=now,
                    last_access=now,
                )
            span.set_attribute("session.id", session_id)
            logger.info("freecad_session_opened", session_id=session_id, name=name)
            return session_id

    def close_session(self, session_id: str) -> bool:
        """Close a session and tear down its document. Returns False if unknown."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        self._doc_closer(session.document)
        logger.info("freecad_session_closed", session_id=session_id)
        return True

    def get(self, session_id: str) -> Session:
        """Return a session, refreshing its idle timer. Raises if missing/expired."""
        now = self._clock()
        with self._lock:
            self._evict_locked(now)
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            session.last_access = now
            return session

    def describe(self, session_id: str) -> dict[str, Any]:
        """Return a JSON-safe summary of a session and its objects."""
        return self.get(session_id).summary()

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions)

    # ---- object registry ----------------------------------------------

    def register_object(
        self,
        session_id: str,
        obj: Any,
        kind: str,
        name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Register a newly-authored object and return its stable obj_id.

        ``metadata`` carries kind-specific structured data — e.g. a joint's
        ``{type, axis, anchor, base, follower}`` for the live solver (MET-530).
        """
        session = self.get(session_id)
        with self._lock:
            session._counter += 1
            obj_id = f"{kind}_{session._counter}"
            # Enforce a meaningful, unique part name (the "parts must be named"
            # rule, behind the MCP so it holds for every client). The caller's
            # explicit name wins; otherwise fall back to the unique obj_id so a
            # part is never anonymous or colliding (e.g. two bare "Box"es). The
            # label is stamped onto the live object so it propagates into STEP
            # PRODUCT entries on export, and from there to the viewer manifest.
            label = name.strip() if name and name.strip() else obj_id
            existing = {e.name for e in session.objects.values()}
            if label in existing:
                label = f"{label}_{session._counter}"
            try:
                if hasattr(obj, "Label"):
                    obj.Label = label
            except Exception:  # noqa: BLE001 — never fail authoring over a label
                logger.warning("freecad_label_set_failed", session_id=session_id, obj_id=obj_id)
            session.objects[obj_id] = ObjectEntry(
                obj_id=obj_id,
                kind=kind,
                name=label,
                obj=obj,
                order=session._counter,
                metadata=metadata or {},
            )
        logger.info(
            "freecad_object_registered",
            session_id=session_id,
            obj_id=obj_id,
            kind=kind,
            label=label,
        )
        return obj_id

    def get_object(self, session_id: str, obj_id: str) -> Any:
        """Return the live FreeCAD object for an obj_id. Raises if missing."""
        return self.get_entry(session_id, obj_id).obj

    def get_entry(self, session_id: str, obj_id: str) -> ObjectEntry:
        """Return the full registry entry (obj + metadata) for an obj_id."""
        session = self.get(session_id)
        entry = session.objects.get(obj_id)
        if entry is None:
            raise ObjectNotFoundError(session_id, obj_id)
        return entry

    def joints(self, session_id: str) -> list[dict[str, Any]]:
        """Joints authored in the session, in the kinematics ``Joint`` shape."""
        return self.get(session_id).joints()

    # ---- internals ----------------------------------------------------

    def _evict_locked(self, now: float) -> None:
        """Drop sessions idle beyond the TTL. Caller holds the lock."""
        if self._ttl <= 0:
            return
        expired = [sid for sid, s in self._sessions.items() if (now - s.last_access) > self._ttl]
        for sid in expired:
            session = self._sessions.pop(sid, None)
            if session is not None:
                self._doc_closer(session.document)
                logger.info("freecad_session_evicted", session_id=sid, reason="idle_ttl")

    def _enforce_capacity_locked(self) -> None:
        """Evict the least-recently-used session if at capacity. Caller holds lock."""
        while len(self._sessions) >= self._max:
            oldest = min(self._sessions.values(), key=lambda s: s.last_access)
            session = self._sessions.pop(oldest.session_id, None)
            if session is not None:
                self._doc_closer(session.document)
                logger.info(
                    "freecad_session_evicted", session_id=oldest.session_id, reason="capacity"
                )
