"""Qt-based chat widget for the MetaForge KiCad plugin.

Implements a ``QDialog`` with a message list, composer, and WebSocket
integration.  The widget is designed to match KiCad's default dark palette.
"""

from __future__ import annotations

import logging
from typing import Optional

from .types import ChatActor, ChatMessage, ChatScope, ChatThread
from .context_resolver import resolve_current_scope
from .ws_client import MetaForgeWSClient, WSConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role -> colour mapping (exported for testability)
# ---------------------------------------------------------------------------

ROLE_COLORS: dict[str, str] = {
    "user": "#264f78",     # Blue tint
    "agent": "#1b5e20",    # Green tint
    "system": "#424242",   # Gray
}

DEFAULT_COLOR = "#424242"


def color_for_role(role: str) -> str:
    """Return the CSS background colour for a message bubble role."""
    return ROLE_COLORS.get(role, DEFAULT_COLOR)


# ---------------------------------------------------------------------------
# Message formatting (pure function, testable without Qt)
# ---------------------------------------------------------------------------


def format_message_html(message: ChatMessage) -> str:
    """Render a ``ChatMessage`` as an HTML snippet for the list widget.

    Parameters
    ----------
    message:
        The chat message to render.

    Returns
    -------
    str
        An HTML string suitable for use in a ``QLabel`` or rich-text
        ``QListWidgetItem``.
    """
    role = message.actor.kind
    name = message.actor.display_name
    bg = color_for_role(role)
    text_color = "#cccccc"

    # System messages are rendered differently.
    if role == "system":
        return (
            f'<div style="text-align:center; color:#9d9d9d; '
            f'font-style:italic; padding:4px;">'
            f"{_escape(message.content)}</div>"
        )

    alignment = "right" if role == "user" else "left"
    return (
        f'<div style="text-align:{alignment}; margin:4px 0;">'
        f'<div style="font-size:10px; color:#9d9d9d;">{_escape(name)}</div>'
        f'<div style="display:inline-block; background:{bg}; '
        f"color:{text_color}; padding:6px 10px; border-radius:8px; "
        f'max-width:80%; text-align:left; font-size:12px;">'
        f"{_escape(message.content)}</div></div>"
    )


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# ChatWidget (QDialog)
# ---------------------------------------------------------------------------


class ChatWidget:
    """Qt-based chat dialog.

    This class wraps all Qt interactions behind a regular Python class so
    that the formatting and connection logic can be unit-tested without
    importing PyQt5.  The actual Qt widgets are created lazily in ``show()``.

    Parameters
    ----------
    gateway_url:
        WebSocket gateway URL (e.g. ``ws://localhost:8000``).
    session_id:
        Session identifier for the WebSocket connection.
    """

    def __init__(
        self,
        gateway_url: str = "ws://localhost:8000",
        session_id: str = "default",
    ) -> None:
        self._gateway_url = gateway_url
        self._session_id = session_id
        self._dialog = None
        self._message_list = None
        self._composer = None
        self._send_button = None
        self._ws_client: Optional[MetaForgeWSClient] = None
        self._current_thread: Optional[ChatThread] = None
        self._scope: ChatScope = resolve_current_scope()
        self._messages: list[ChatMessage] = []

    # ---- Public API -------------------------------------------------------

    def show(self) -> None:
        """Create and show the chat dialog.

        Requires PyQt5 at runtime (available inside KiCad).
        """
        try:
            from PyQt5.QtWidgets import (  # type: ignore[import-untyped]
                QDialog,
                QVBoxLayout,
                QHBoxLayout,
                QListWidget,
                QListWidgetItem,
                QTextEdit,
                QPushButton,
                QLabel,
            )
            from PyQt5.QtCore import Qt  # type: ignore[import-untyped]
            from PyQt5.QtGui import QColor, QPalette  # type: ignore[import-untyped]
        except ImportError:
            logger.error("PyQt5 is required to display the chat widget")
            return

        # ---- Dialog setup -------------------------------------------------
        self._dialog = QDialog()
        self._dialog.setWindowTitle("MetaForge Chat")
        self._dialog.resize(420, 560)

        # Dark palette matching KiCad.
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#1e1e1e"))
        palette.setColor(QPalette.WindowText, QColor("#cccccc"))
        palette.setColor(QPalette.Base, QColor("#252526"))
        palette.setColor(QPalette.Text, QColor("#cccccc"))
        palette.setColor(QPalette.Button, QColor("#3c3c3c"))
        palette.setColor(QPalette.ButtonText, QColor("#cccccc"))
        self._dialog.setPalette(palette)

        layout = QVBoxLayout(self._dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Scope label.
        scope_label = QLabel(self._scope.label or "Project")
        scope_label.setStyleSheet(
            "color: #9d9d9d; font-size: 11px; padding: 2px 0;"
        )
        layout.addWidget(scope_label)

        # ---- Message list -------------------------------------------------
        self._message_list = QListWidget()
        self._message_list.setStyleSheet(
            "QListWidget { background: #1e1e1e; border: none; }"
            "QListWidget::item { border: none; padding: 2px 4px; }"
        )
        layout.addWidget(self._message_list, stretch=1)

        # ---- Composer row -------------------------------------------------
        composer_layout = QHBoxLayout()

        self._composer = QTextEdit()
        self._composer.setPlaceholderText("Type a message...")
        self._composer.setMaximumHeight(60)
        self._composer.setStyleSheet(
            "QTextEdit { background: #3c3c3c; color: #cccccc; "
            "border: 1px solid #3c3c3c; border-radius: 4px; "
            "padding: 4px 8px; font-size: 12px; }"
        )
        composer_layout.addWidget(self._composer, stretch=1)

        self._send_button = QPushButton("Send")
        self._send_button.setStyleSheet(
            "QPushButton { background: #0078d4; color: #ffffff; "
            "border: none; border-radius: 4px; padding: 6px 14px; "
            "font-size: 12px; }"
            "QPushButton:hover { background: #1a8ceb; }"
        )
        self._send_button.clicked.connect(self._on_send_clicked)
        composer_layout.addWidget(self._send_button)

        layout.addLayout(composer_layout)

        # ---- WebSocket ----------------------------------------------------
        config = WSConfig(
            gateway_url=self._gateway_url,
            session_id=self._session_id,
        )
        self._ws_client = MetaForgeWSClient(
            config=config,
            on_message=self._on_ws_message,
        )
        self._ws_client.connect()

        # ---- Show ---------------------------------------------------------
        self._dialog.exec_()

    def close(self) -> None:
        """Close the dialog and WebSocket."""
        if self._ws_client:
            self._ws_client.disconnect()
        if self._dialog:
            self._dialog.close()

    def add_message(self, message: ChatMessage) -> None:
        """Add a message to the internal list and update the UI."""
        self._messages.append(message)
        self._render_message(message)

    # ---- Internal ---------------------------------------------------------

    def _on_send_clicked(self) -> None:
        """Handle send button click."""
        if self._composer is None:
            return
        content = self._composer.toPlainText().strip()
        if not content:
            return

        # Create and display the user message locally.
        actor = ChatActor(kind="user", display_name="You")
        msg = ChatMessage.create(
            thread_id=self._current_thread.id if self._current_thread else "",
            actor=actor,
            content=content,
        )
        self.add_message(msg)

        # Send via WebSocket.
        if self._ws_client and self._current_thread:
            self._ws_client.send_message(self._current_thread.id, content)

        # Clear composer.
        self._composer.clear()

    def _on_ws_message(self, message: ChatMessage) -> None:
        """Handle incoming WebSocket message."""
        self.add_message(message)

    def _render_message(self, message: ChatMessage) -> None:
        """Render a message into the QListWidget."""
        if self._message_list is None:
            return
        try:
            from PyQt5.QtWidgets import QListWidgetItem, QLabel  # type: ignore[import-untyped]
            from PyQt5.QtCore import Qt  # type: ignore[import-untyped]

            html = format_message_html(message)
            label = QLabel(html)
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            label.setStyleSheet("background: transparent;")

            item = QListWidgetItem()
            item.setSizeHint(label.sizeHint())
            self._message_list.addItem(item)
            self._message_list.setItemWidget(item, label)
            self._message_list.scrollToBottom()
        except ImportError:
            pass
