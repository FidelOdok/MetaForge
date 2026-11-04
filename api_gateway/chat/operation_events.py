"""A chat turn as an operation on its project's stream (FORGE-788, RT-001).

A turn sent to a project-scoped thread is one operation, ``chat:<turn id>``:

* ``accepted`` the moment the person's message is stored, so the view can
  acknowledge it with an operation id before any agent work starts;
* ``started`` when the agent begins the turn;
* ``completed`` when it replied, ``failed`` when the reply is an error, and
  ``cancelled`` when the person's page went away and the turn was stopped.

The turn id is the client's ``client_turn_id`` when it sends one (so the view
knows the operation id before the reply returns), else the stored message's
id. Every message on these events is written here: never the prompt, the
reply or a tool's arguments. A turn in a thread with no project has no
stream to report on, and reports nothing.

A chat turn is not cancellable or retryable from the operations view; the
person sends the message again.
"""

from __future__ import annotations

import re

import structlog

from api_gateway.operations import publisher as operations

logger = structlog.get_logger(__name__)

__all__ = [
    "TITLE",
    "chat_operation_id",
    "turn_accepted",
    "turn_cancelled",
    "turn_finished",
    "turn_started",
]

TITLE = "Reply to your message"
_CLIENT_TURN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


def chat_operation_id(project_id: str, message_id: str, client_turn_id: str | None) -> str:
    """``chat:<client turn id>`` when it is well formed and unused, else ``chat:<message id>``."""
    if client_turn_id and _CLIENT_TURN_ID.match(client_turn_id):
        candidate = f"chat:{client_turn_id}"
        try:
            known = operations.get_operation_publisher().store.get(project_id, candidate)
        except Exception:  # noqa: BLE001
            known = None
        if known is None:
            return candidate
    return f"chat:{message_id}"


def _publish(project_id: str, op: str, event_type: str, state: str, **fields: object) -> None:
    operations.publish(
        project_id, op, event_type, state, kind="chat", title=TITLE, domain="general", **fields
    )


def turn_accepted(project_id: str, op: str) -> None:
    """The person's message is stored; the turn is acknowledged. Never raises."""
    _publish(project_id, op, "accepted", "queued", message="Message received")


def turn_started(project_id: str, op: str) -> None:
    _publish(project_id, op, "started", "running", message="Working on a reply")


def turn_finished(project_id: str, op: str, *, failed: bool, replied: bool = True) -> None:
    """The turn ended: replied, failed, or had no agent to answer it."""
    if failed:
        _publish(
            project_id,
            op,
            "failed",
            "failed",
            severity="error",
            message="The reply could not be completed; send the message again",
        )
    else:
        _publish(
            project_id,
            op,
            "completed",
            "completed",
            message="Replied" if replied else "Message saved; no agent is set up to reply",
        )


def turn_cancelled(project_id: str, op: str) -> None:
    _publish(
        project_id, op, "cancelled", "cancelled", message="Stopped because the page was closed"
    )
