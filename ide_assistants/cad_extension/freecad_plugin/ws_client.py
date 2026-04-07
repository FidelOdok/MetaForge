"""WebSocket client connecting the FreeCAD plugin to the MetaForge gateway."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_GATEWAY_URL = os.environ.get("METAFORGE_GATEWAY_URL", "http://localhost:8000")


class GatewayWebSocketClient:
    """Thread-safe WebSocket client for the FreeCAD plugin."""

    def __init__(self, gateway_url: str = DEFAULT_GATEWAY_URL) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._ws_url = (
            self._gateway_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/chat"
        )
        self._thread: threading.Thread | None = None
        self._send_queue: queue.Queue[str] = queue.Queue()
        self._on_message: Callable[[dict], None] | None = None  # type: ignore[type-arg]
        self._running = False

    def set_message_handler(self, handler: Callable[[dict], None]) -> None:  # type: ignore[type-arg]
        self._on_message = handler

    def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._running = False

    def send(self, message: dict) -> None:  # type: ignore[type-arg]
        self._send_queue.put(json.dumps(message))

    def _run(self) -> None:
        try:
            import websocket  # type: ignore[import-not-found,unused-ignore]
        except ImportError:
            logger.warning("freecad_plugin_ws_websocket_not_available")
            return

        def on_message(ws: object, data: str) -> None:
            if self._on_message:
                try:
                    self._on_message(json.loads(data))
                except Exception as exc:
                    logger.warning("freecad_plugin_ws_message_parse_error", error=str(exc))

        def on_error(ws: object, error: object) -> None:
            logger.warning("freecad_plugin_ws_error", error=str(error))

        ws_app = websocket.WebSocketApp(  # type: ignore[attr-defined,unused-ignore]
            self._ws_url,
            on_message=on_message,
            on_error=on_error,
        )

        while self._running:
            try:
                while not self._send_queue.empty():
                    msg = self._send_queue.get_nowait()
                    ws_app.send(msg)
                time.sleep(0.05)
            except Exception:
                pass
