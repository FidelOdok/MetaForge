"""Whose library the current work sees (FORGE-876).

The library stores (:mod:`~twin_core.library.catalog`,
:mod:`~twin_core.library.machines`) hold shared entries and, on a gateway
shared by several accounts, each account's own. A store call that is not told
whose view to use reads it from here, so every reader inside a request (a
design flow being planned, the Imagine studio, the manufacturing context)
sees what the request's caller may see without each passing it along.

``None``, the default, is an unscoped view: a local gateway, or the
deployment's own services. Everything is visible and writes are shared.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

__all__ = ["FROM_CONTEXT", "NO_TENANT", "current_viewer", "library_viewer", "resolve"]

#: A scoped caller with no tenant: owns nothing, so sees only shared entries.
NO_TENANT: Final = "\x00no-tenant"


class _FromContext:
    def __repr__(self) -> str:
        return "FROM_CONTEXT"


#: Default for a store's ``viewer``/``owner``/``editor``: use the current view.
FROM_CONTEXT: Final = _FromContext()

_viewer: ContextVar[str | None] = ContextVar("library_viewer", default=None)


def current_viewer() -> str | None:
    return _viewer.get()


@contextmanager
def library_viewer(tenant: str | None) -> Iterator[None]:
    """Make ``tenant`` the view of every store call in the block."""
    token = _viewer.set(tenant)
    try:
        yield
    finally:
        _viewer.reset(token)


def resolve(value: str | None | _FromContext) -> str | None:
    return current_viewer() if isinstance(value, _FromContext) else value
