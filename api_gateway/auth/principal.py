"""The authenticated caller.

MetaForge has never had one of these. Until now the closest thing was
``actor_id`` — a free-text string like ``"tui-user"`` that clients supplied and
nothing verified, carried on chat messages and MCP call contexts.

A :class:`Principal` is the opposite: it exists only as the result of verifying
a signature, and it is never constructed from request input. Code that wants to
know *who is calling* should depend on this; code that merely wants a label for
a trace or an audit line can keep using ``actor_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Principal"]


@dataclass(frozen=True, slots=True)
class Principal:
    """A caller whose identity has been cryptographically verified."""

    #: Stable user identifier — the JWT ``sub`` claim. For Supabase this is the
    #: ``auth.users.id`` UUID, and it is the value to join on.
    subject: str

    #: Email, when the token carries one. Display only: users can change it,
    #: so never key authorisation off this.
    email: str | None = None

    #: Supabase ``role`` claim (typically ``authenticated``). Not a MetaForge
    #: role — account-level roles live in ``account_members``.
    role: str | None = None

    #: Remaining verified claims, for callers that need something niche without
    #: this class growing a field per claim.
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def actor_id(self) -> str:
        """Render as the ``<kind>:<name>`` actor string used across MetaForge.

        Lets a verified principal flow into the existing session-capture and
        trace plumbing, which already speaks ``actor_id``, without those call
        sites having to learn about principals.
        """
        return f"user:{self.subject}"
