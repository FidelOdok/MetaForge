"""FreeCAD chat widget — Qt dock panel for MetaForge agent chat."""

# mypy: warn-unused-ignores = False
from __future__ import annotations

import structlog

from ide_assistants.cad_extension.freecad_plugin.context_resolver import resolve_current_scope
from ide_assistants.cad_extension.freecad_plugin.ws_client import GatewayWebSocketClient

logger = structlog.get_logger(__name__)


class ChatWidget:
    """MetaForge chat panel embedded in FreeCAD.

    Instantiated by plugin.py when the user opens the assistant.
    Uses PySide2/Qt (bundled with FreeCAD) for the UI.
    """

    def __init__(self, gateway_url: str) -> None:
        self._client = GatewayWebSocketClient(gateway_url)
        self._widget: object | None = None

    def show(self) -> None:
        """Open or raise the chat dock panel."""
        try:
            self._build_widget()
            self._client.connect()
        except Exception as exc:
            logger.warning("freecad_chat_widget_show_error", error=str(exc))

    def hide(self) -> None:
        self._client.disconnect()

    def _build_widget(self) -> None:
        """Build the Qt dock widget. No-op if Qt unavailable."""
        try:
            from PySide2 import QtWidgets  # type: ignore[import-not-found,unused-ignore]
        except ImportError:
            try:
                from PySide6 import QtWidgets  # type: ignore[import-not-found,unused-ignore]
            except ImportError:
                logger.warning("freecad_chat_widget_no_qt")
                return

        if self._widget is not None:
            return

        dock = QtWidgets.QDockWidget("MetaForge Assistant")
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        # Scope label
        scope = resolve_current_scope()
        scope_label = QtWidgets.QLabel(f"Context: {scope.kind} — {scope.label}")
        layout.addWidget(scope_label)

        # Message list
        self._msg_list = QtWidgets.QListWidget()
        layout.addWidget(self._msg_list)

        # Input row
        input_row = QtWidgets.QHBoxLayout()
        self._input = QtWidgets.QLineEdit()
        self._input.setPlaceholderText("Ask the Mechanical agent…")
        send_btn = QtWidgets.QPushButton("Send")
        send_btn.clicked.connect(self._send)
        input_row.addWidget(self._input)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        dock.setWidget(container)
        self._widget = dock

        def on_message(msg: dict) -> None:  # type: ignore[type-arg]
            content = msg.get("content", str(msg))
            self._msg_list.addItem(f"Agent: {content}")

        self._client.set_message_handler(on_message)

    def _send(self) -> None:
        if not hasattr(self, "_input"):
            return
        text = self._input.text().strip()
        if not text:
            return
        scope = resolve_current_scope()
        self._client.send(
            {
                "type": "chat",
                "content": text,
                "scope": {"kind": scope.kind, "entity_id": scope.entity_id},
                "agent": "ME",
            }
        )
        if hasattr(self, "_msg_list"):
            self._msg_list.addItem(f"You: {text}")
        self._input.clear()
