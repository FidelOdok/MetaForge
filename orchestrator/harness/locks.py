"""Session write-lock (MET-547, Phase 3).

Guards against two writers mutating the same session at once (e.g. an
external harness and the in-house runtime both driving one run). The lock is
advisory and *fail-fast*: acquiring a session already held by a different
holder raises :class:`SessionLockedError` so the caller can surface a clean
409 rather than silently interleaving writes.

Re-entrant for the same holder (tracked by count), so nested
:meth:`SessionWriteLock.hold` blocks by the same owner compose correctly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


class SessionLockedError(RuntimeError):
    """A session is write-locked by a different holder."""

    def __init__(self, session_id: str, holder: str) -> None:
        self.session_id = session_id
        self.holder = holder
        super().__init__(f"session '{session_id}' is write-locked by '{holder}'")


@dataclass(frozen=True)
class LockToken:
    """Proof of holding a session's write lock."""

    session_id: str
    holder: str


class SessionWriteLock:
    """One-writer-per-session advisory lock, re-entrant per holder."""

    def __init__(self) -> None:
        # session_id -> (holder, reentrancy count)
        self._held: dict[str, tuple[str, int]] = {}

    def acquire(self, session_id: str, holder: str) -> LockToken:
        current = self._held.get(session_id)
        if current is not None and current[0] != holder:
            logger.warning(
                "session_lock_contended",
                session_id=session_id,
                holder=holder,
                held_by=current[0],
            )
            raise SessionLockedError(session_id, current[0])
        count = current[1] + 1 if current is not None else 1
        self._held[session_id] = (holder, count)
        return LockToken(session_id=session_id, holder=holder)

    def release(self, token: LockToken) -> None:
        current = self._held.get(token.session_id)
        if current is None or current[0] != token.holder:
            raise SessionLockedError(
                token.session_id,
                current[0] if current is not None else token.holder,
            )
        count = current[1] - 1
        if count <= 0:
            del self._held[token.session_id]
        else:
            self._held[token.session_id] = (token.holder, count)

    def is_held(self, session_id: str) -> bool:
        return session_id in self._held

    def holder(self, session_id: str) -> str | None:
        current = self._held.get(session_id)
        return current[0] if current is not None else None

    @contextmanager
    def hold(self, session_id: str, holder: str) -> Iterator[LockToken]:
        token = self.acquire(session_id, holder)
        try:
            yield token
        finally:
            self.release(token)
