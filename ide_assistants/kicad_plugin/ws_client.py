"""WebSocket client for communication with the MetaForge gateway.

Uses ``QWebSocket`` from PyQt5 so it integrates naturally with KiCad's
Qt event loop.  Provides auto-reconnect on disconnect and thread
management helpers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .types import ChatMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_GATEWAY_URL = "ws://localhost:8000"
WS_PATH_TEMPLATE = "/api/v1/assistant/ws/{session_id}"
RECONNECT_INTERVAL_MS = 5_000
MAX_RECONNECT_ATTEMPTS = 10


@dataclass
class WSConfig:
    """WebSocket connection configuration."""

    gateway_url: str = DEFAULT_GATEWAY_URL
    session_id: str = ""
    reconnect_interval_ms: int = RECONNECT_INTERVAL_MS
    max_reconnect_attempts: int = MAX_RECONNECT_ATTEMPTS


# ---------------------------------------------------------------------------
# Message serialisation helpers (pure functions, testable)
# ---------------------------------------------------------------------------


def build_ws_url(gateway_url: str, session_id: str) -> str:
    """Build the full WebSocket URL for a given session.

    Parameters
    ----------
    gateway_url:
        Base WebSocket URL (e.g. ``ws://localhost:8000``).
    session_id:
        Session identifier.

    Returns
    -------
    str
        The complete WebSocket endpoint URL.
    """
    base = gateway_url.rstrip("/")
    path = WS_PATH_TEMPLATE.format(session_id=session_id)
    return f"{base}{path}"


def serialize_send_message(thread_id: str, content: str) -> str:
    """Serialize a ``send_message`` command to JSON.

    Parameters
    ----------
    thread_id:
        The target thread ID.
    content:
        The message text to send.

    Returns
    -------
    str
        JSON string ready to be sent over the WebSocket.
    """
    return json.dumps(
        {
            "type": "send_message",
            "threadId": thread_id,
            "content": content,
        }
    )


def serialize_create_thread(title: str, scope_kind: str, entity_id: str = "") -> str:
    """Serialize a ``create_thread`` command to JSON."""
    payload: dict = {
        "type": "create_thread",
        "title": title,
        "scope": {"kind": scope_kind},
    }
    if entity_id:
        payload["scope"]["entityId"] = entity_id
    return json.dumps(payload)


def deserialize_message(raw: str) -> ChatMessage | None:
    """Attempt to parse a gateway WebSocket frame into a ChatMessage.

    Returns ``None`` if the frame is not a message payload.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to decode WebSocket frame")
        return None

    msg_type = data.get("type")
    if msg_type == "message" and "message" in data:
        return ChatMessage.from_dict(data["message"])
    # Some frames carry the message fields at the top level.
    if "content" in data and "actor" in data:
        return ChatMessage.from_dict(data)

    return None


# ---------------------------------------------------------------------------
# Qt WebSocket client (requires PyQt5 at runtime)
# ---------------------------------------------------------------------------


class MetaForgeWSClient:
    """WebSocket client backed by ``QWebSocket``.

    Designed to run inside the KiCad Qt event loop.  Handles automatic
    reconnection and exposes a simple callback-based API.

    Parameters
    ----------
    config:
        Connection configuration.
    on_message:
        Callback invoked when a ``ChatMessage`` is received.
    on_connected:
        Callback invoked on successful (re)connection.
    on_disconnected:
        Callback invoked when the connection drops.
    """

    def __init__(
        self,
        config: WSConfig,
        on_message: Callable[[ChatMessage], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._reconnect_count = 0
        self._socket = None  # Lazy-initialised
        self._reconnect_timer = None

    # ---- Public -----------------------------------------------------------

    @property
    def url(self) -> str:
        return build_ws_url(self._config.gateway_url, self._config.session_id)

    def connect(self) -> None:
        """Open the WebSocket connection."""
        try:
            from PyQt5.QtCore import QUrl  # type: ignore[import-untyped]
            from PyQt5.QtWebSockets import QWebSocket  # type: ignore[import-untyped]
        except ImportError:
            logger.error("PyQt5 is required for the KiCad WebSocket client")
            return

        if self._socket is None:
            self._socket = QWebSocket()
            self._socket.connected.connect(self._handle_connected)
            self._socket.disconnected.connect(self._handle_disconnected)
            self._socket.textMessageReceived.connect(self._handle_text_message)

        url = QUrl(self.url)
        logger.info("Connecting to %s", url.toString())
        self._socket.open(url)

    def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._reconnect_timer is not None:
            self._reconnect_timer.stop()
            self._reconnect_timer = None
        if self._socket is not None:
            self._socket.close()

    def send(self, payload: str) -> None:
        """Send a raw JSON string over the WebSocket."""
        if self._socket is not None:
            self._socket.sendTextMessage(payload)

    def send_message(self, thread_id: str, content: str) -> None:
        """Send a chat message to a thread."""
        self.send(serialize_send_message(thread_id, content))

    def create_thread(self, title: str, scope_kind: str, entity_id: str = "") -> None:
        """Request creation of a new chat thread."""
        self.send(serialize_create_thread(title, scope_kind, entity_id))

    # ---- Internal ---------------------------------------------------------

    def _handle_connected(self) -> None:
        self._reconnect_count = 0
        logger.info("WebSocket connected")
        if self._on_connected:
            self._on_connected()

    def _handle_disconnected(self) -> None:
        logger.info("WebSocket disconnected")
        if self._on_disconnected:
            self._on_disconnected()
        self._attempt_reconnect()

    def _handle_text_message(self, raw: str) -> None:
        msg = deserialize_message(raw)
        if msg is not None and self._on_message:
            self._on_message(msg)

    def _attempt_reconnect(self) -> None:
        if self._reconnect_count >= self._config.max_reconnect_attempts:
            logger.warning("Max reconnection attempts reached")
            return

        self._reconnect_count += 1
        logger.info(
            "Reconnecting in %d ms (attempt %d/%d)",
            self._config.reconnect_interval_ms,
            self._reconnect_count,
            self._config.max_reconnect_attempts,
        )

        try:
            from PyQt5.QtCore import QTimer  # type: ignore[import-untyped]

            self._reconnect_timer = QTimer()
            self._reconnect_timer.setSingleShot(True)
            self._reconnect_timer.timeout.connect(self.connect)
            self._reconnect_timer.start(self._config.reconnect_interval_ms)
        except ImportError:
            pass
