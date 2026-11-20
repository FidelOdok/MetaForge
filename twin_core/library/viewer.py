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

__all__ = [
    "FROM_CONTEXT",
    "NO_TENANT",
    "VIEW_HEADER",
    "current_viewer",
    "decode_view",
    "encode_view",
    "library_viewer",
    "resolve",
]

#: The library view a trusted service forwards with its own credential: the MCP
#: sidecar calls the gateway as the deployment, on behalf of a signed-in
#: account, and the gateway applies that account's view only because the
#: request came with the service key (api_gateway.auth.middleware).
VIEW_HEADER: Final = "X-MetaForge-Library-View"
_NO_TENANT_WIRE: Final = "no-tenant"

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


def encode_view(view: str | None) -> str | None:
    """The header value for ``view``, or ``None`` to send no header (unscoped).

    A tenant is an opaque key the auth provider chose, so it is base64url
    encoded rather than trusted to be header-safe; ``NO_TENANT`` has its own
    word because it carries a NUL byte.
    """
    import base64

    if view is None:
        return None
    if view == NO_TENANT:
        return _NO_TENANT_WIRE
    return "t." + base64.urlsafe_b64encode(view.encode()).decode().rstrip("=")


def decode_view(value: str | None) -> str | None:
    """The view a header value names; ``NO_TENANT`` for anything malformed.

    Malformed is never read as unscoped: that would show and write everything.
    """
    import base64
    import binascii

    if value is None:
        return None
    value = value.strip()
    if value.startswith("t."):
        raw = value[2:]
        try:
            tenant = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return NO_TENANT
        return tenant or NO_TENANT
    return NO_TENANT
