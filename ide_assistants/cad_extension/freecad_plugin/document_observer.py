"""FreeCAD DocumentObserver — save hook and live patch receiver."""

# mypy: warn-unused-ignores = False
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any

import httpx
import structlog

from ide_assistants.cad_extension.freecad_plugin.types import PatchOp

logger = structlog.get_logger(__name__)

GATEWAY_URL = os.environ.get("METAFORGE_GATEWAY_URL", "http://localhost:8000")
PATCH_SERVER_PORT = int(os.environ.get("METAFORGE_FREECAD_PATCH_PORT", "9001"))
DEBOUNCE_SECONDS = 0.5


class DocumentObserver:
    """Observes FreeCAD document events and syncs to the MetaForge Twin.

    Install via: App.ActiveDocument.addObserver(DocumentObserver())
    """

    def __init__(self) -> None:
        self._last_save: float = 0.0
        self._debounce_timer: threading.Timer | None = None

    def slotFinishSaveDocument(self, doc: Any) -> None:  # noqa: N802
        """Called by FreeCAD after a document is saved."""
        file_path = getattr(doc, "FileName", "")
        if not file_path:
            return
        # Debounce rapid saves
        if self._debounce_timer:
            self._debounce_timer.cancel()
        self._debounce_timer = threading.Timer(
            DEBOUNCE_SECONDS,
            self._trigger_sync,
            args=[file_path],
        )
        self._debounce_timer.start()

    def _trigger_sync(self, file_path: str) -> None:
        """POST /sync for the Twin node linked to this file path."""
        try:
            # Find the linked node via GET /v1/twin/links
            resp = httpx.get(f"{GATEWAY_URL}/v1/twin/links", timeout=5.0)
            if resp.status_code != 200:
                return
            links = resp.json()
            for link in links:
                if link.get("source_path") == file_path:
                    node_id = link["work_product_id"]
                    sync_resp = httpx.post(
                        f"{GATEWAY_URL}/v1/twin/nodes/{node_id}/sync",
                        timeout=10.0,
                    )
                    logger.info(
                        "freecad_plugin_sync_triggered",
                        file_path=file_path,
                        node_id=node_id,
                        status=sync_resp.status_code,
                    )
                    return
        except Exception as exc:
            logger.warning("freecad_plugin_sync_failed", error=str(exc))


class PatchServer:
    """Local WebSocket server that receives apply_patch commands from the gateway
    and applies them to the active FreeCAD document.

    Listens on localhost:9001 by default.
    """

    def __init__(self, port: int = PATCH_SERVER_PORT) -> None:
        self._port = port
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("freecad_plugin_patch_server_started", port=self._port)

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            logger.warning("freecad_plugin_patch_server_error", error=str(exc))

    async def _serve(self) -> None:
        try:
            import websockets  # type: ignore[import-not-found,unused-ignore]
        except ImportError:
            logger.warning("freecad_plugin_patch_server_websockets_not_installed")
            return

        async def handle(websocket: Any) -> None:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    op = PatchOp(
                        op=msg["op"],
                        object_name=msg.get("object", ""),
                        property=msg.get("property"),
                        value=msg.get("value"),
                    )
                    self._apply(op)
                except Exception as exc:
                    logger.warning("freecad_plugin_patch_apply_error", error=str(exc))

        async with websockets.serve(handle, "localhost", self._port):  # type: ignore
            while self._running:
                await asyncio.sleep(0.1)

    def _apply(self, op: PatchOp) -> None:
        """Apply a patch operation to the active FreeCAD document."""
        try:
            import FreeCAD  # type: ignore

            doc = FreeCAD.ActiveDocument
            if doc is None:
                return
            if op.op == "set_property":
                obj = doc.getObject(op.object_name)
                if obj and op.property:
                    setattr(obj, op.property, op.value)
                    doc.recompute()
            elif op.op == "recompute":
                doc.recompute()
            elif op.op == "remove_feature":
                obj = doc.getObject(op.object_name)
                if obj:
                    doc.removeObject(op.object_name)
                    doc.recompute()
            logger.info("freecad_plugin_patch_applied", op=op.op, object=op.object_name)
        except Exception as exc:
            logger.warning("freecad_plugin_patch_freecad_error", error=str(exc))
