"""Per-call MCP context — project_id / session_id / actor_id rideshare (MET-387).

Every MCP tool call carries metadata that the harness has but the
backends don't (which project? which agent? which session?). This
module:

* Defines ``McpCallContext`` — the typed payload that rides along.
* Exposes a ``ContextVar`` so handlers reach it without a parameter
  on every signature: ``ctx = current_context()``.
* Provides ``set_context()`` / ``with_context()`` so transports can
  install a context for the duration of a request.
* Parses the context out of HTTP headers (``X-MetaForge-*``) and
  stdio env vars (``METAFORGE_*``).

Layer-1 invariant: this module imports only stdlib + pydantic. No
upstream tool / agent imports. The transports + tool adapters wire
the context in; this module never reaches up.
"""

from __future__ import annotations

import os
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# --- Type --------------------------------------------------------------------


class McpCallContext(BaseModel):
    """Metadata for a single MCP tool call.

    All fields are optional except ``session_id`` so the context can
    represent both authenticated harness calls (full quartet) and
    legacy unscoped calls (everything ``None``). ``actor_id`` follows
    the convention ``<kind>:<name>`` — e.g. ``user:fidel``,
    ``agent:claude_code``, ``system:cron``.
    """

    project_id: UUID | None = Field(
        default=None,
        description=(
            "The MetaForge project the call should scope to. None = all projects (admin-only)."
        ),
    )
    session_id: UUID = Field(
        default_factory=uuid.uuid4,
        description="Stable per-harness-connection id. Auto-generated when not supplied.",
    )
    actor_id: str = Field(
        default="system:unattributed",
        description="Who initiated the call. Format: <kind>:<name>.",
    )
    correlation_id: UUID = Field(
        default_factory=uuid.uuid4,
        description="Per-request id for tying back to the harness's own trace.",
    )

    model_config = ConfigDict(frozen=True)


# Sentinel context used when the caller didn't install one. Encodes the
# legacy "unscoped, unattributed" behaviour explicitly so handlers can
# log it instead of silently treating the absence as a bug.
_DEFAULT_CONTEXT = McpCallContext(
    project_id=None,
    session_id=UUID("00000000-0000-0000-0000-000000000000"),
    actor_id="system:unattributed",
    correlation_id=UUID("00000000-0000-0000-0000-000000000000"),
)


# --- ContextVar plumbing ----------------------------------------------------


_current: ContextVar[McpCallContext] = ContextVar("mcp_call_context", default=_DEFAULT_CONTEXT)


def current_context() -> McpCallContext:
    """Return the active call context. Falls back to a sentinel."""
    return _current.get()


def set_context(ctx: McpCallContext) -> Token[McpCallContext]:
    """Install ``ctx`` as the active context. Returns the token to reset."""
    return _current.set(ctx)


def reset_context(token: Token[McpCallContext]) -> None:
    """Reset the context to whatever was active before ``set_context``."""
    _current.reset(token)


@contextmanager
def with_context(ctx: McpCallContext) -> Iterator[McpCallContext]:
    """Scope a block of code to a call context.

    >>> async with with_context(ctx):
    ...     await tool.handle_request(payload)

    Used by transports to wrap each request so handlers downstream see
    the right ``current_context()``.
    """
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


# --- Header / env parsing ---------------------------------------------------


# HTTP / SSE — client sends these headers on every /mcp post.
HEADER_PROJECT = "X-MetaForge-Project"
HEADER_SESSION = "X-MetaForge-Session"
HEADER_ACTOR = "X-MetaForge-Actor"
HEADER_CORRELATION = "X-MetaForge-Correlation"

# Stdio — client passes these env vars at spawn (see .mcp.json).
ENV_PROJECT = "METAFORGE_PROJECT_ID"
ENV_SESSION = "METAFORGE_SESSION_ID"
ENV_ACTOR = "METAFORGE_ACTOR_ID"
ENV_CORRELATION = "METAFORGE_CORRELATION_ID"


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


# --- Session-scoped project binding (MET-680) --------------------------------
#
# CLAUDE.md documents two equivalent ways to "set your active project":
# `metaforge-capture use <id>` (CLI) or `session.start(project_id=...)`. Only
# the first worked. `session.start` forwarded project_id into the agent-session
# capture store for attribution and never touched the MCP call context, so
# every later knowledge.*/twin.* call in that "session" still resolved to
# whatever the transport stamped per call -- usually nothing, i.e. the default
# tenant.
#
# The binding is keyed on the call context's ``session_id``, never global.
# That matters: the HTTP sidecar serves several clients at once, so a
# process-wide "last project started" would leak one client's project into
# another's calls -- a correctness bug worse than the one being fixed. Keying
# on the session means a client that supplies no session identity simply gets
# no binding (its session_id is freshly generated per call and can never match
# a stored key), which is the safe failure mode.
#
# Consequence, and it is a real limitation: this only takes effect for callers
# that present a STABLE session -- stdio (``METAFORGE_SESSION_ID``, one
# process = one session) or HTTP clients sending ``X-MetaForge-Session``. A
# plain HTTP client that sends neither must still pass ``project_id`` per call.

_MAX_SESSION_BINDINGS = 512
"""Cap the registry so a long-lived sidecar cannot grow it without bound.
Oldest binding is evicted first; a session whose binding is evicted degrades to
per-call project_id rather than to a wrong project."""

_session_projects: OrderedDict[UUID, UUID] = OrderedDict()


def bind_session_project(session_id: UUID, project_id: UUID) -> None:
    """Scope every later call carrying ``session_id`` to ``project_id``."""
    _session_projects.pop(session_id, None)
    _session_projects[session_id] = project_id
    while len(_session_projects) > _MAX_SESSION_BINDINGS:
        _session_projects.popitem(last=False)


def bound_project(session_id: UUID | None) -> UUID | None:
    """The project bound to ``session_id``, if any."""
    if session_id is None:
        return None
    return _session_projects.get(session_id)


def clear_session_project(session_id: UUID) -> None:
    """Drop a session's binding (called when the session completes)."""
    _session_projects.pop(session_id, None)


def reset_session_projects() -> None:
    """Clear every binding — tests only."""
    _session_projects.clear()


def _apply_session_binding(fields: dict[str, object], session: UUID | None) -> None:
    """Fill in a session-bound project when the caller supplied none.

    An explicit per-call project always wins: a caller naming a project on the
    call is being more specific than a binding made earlier.
    """
    if fields.get("project_id") is not None or session is None:
        return
    bound = bound_project(session)
    if bound is not None:
        fields["project_id"] = bound


def context_from_headers(headers: dict[str, str] | None) -> McpCallContext:
    """Build a context from HTTP request headers.

    Missing headers fall back to the McpCallContext defaults
    (auto-generated session_id + correlation_id, ``system:unattributed`` actor).
    Unparseable UUIDs are silently dropped — the safer behaviour for a
    public surface.
    """
    h = headers or {}
    # Headers are case-insensitive on the wire but Python dicts aren't.
    # Build a folded view so X-MetaForge-Project, x-metaforge-project, etc.
    # all resolve.
    folded = {k.lower(): v for k, v in h.items()}
    project = _parse_uuid(folded.get(HEADER_PROJECT.lower()))
    session = _parse_uuid(folded.get(HEADER_SESSION.lower()))
    correlation = _parse_uuid(folded.get(HEADER_CORRELATION.lower()))
    actor = folded.get(HEADER_ACTOR.lower()) or "system:unattributed"
    fields: dict[str, object] = {"project_id": project, "actor_id": actor}
    if session is not None:
        fields["session_id"] = session
    if correlation is not None:
        fields["correlation_id"] = correlation
    _apply_session_binding(fields, session)
    return McpCallContext(**fields)


def context_from_env(env: dict[str, str] | None = None) -> McpCallContext:
    """Build a context from environment variables (stdio transport)."""
    e = env if env is not None else os.environ
    project = _parse_uuid(e.get(ENV_PROJECT))
    session = _parse_uuid(e.get(ENV_SESSION))
    correlation = _parse_uuid(e.get(ENV_CORRELATION))
    actor = e.get(ENV_ACTOR) or "system:unattributed"
    fields: dict[str, object] = {"project_id": project, "actor_id": actor}
    if session is not None:
        fields["session_id"] = session
    if correlation is not None:
        fields["correlation_id"] = correlation
    _apply_session_binding(fields, session)
    return McpCallContext(**fields)


__all__ = [
    "ENV_ACTOR",
    "ENV_CORRELATION",
    "ENV_PROJECT",
    "ENV_SESSION",
    "HEADER_ACTOR",
    "HEADER_CORRELATION",
    "HEADER_PROJECT",
    "HEADER_SESSION",
    "McpCallContext",
    "bind_session_project",
    "bound_project",
    "clear_session_project",
    "context_from_env",
    "context_from_headers",
    "current_context",
    "reset_context",
    "set_context",
    "with_context",
]
