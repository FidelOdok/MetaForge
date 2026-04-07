"""MetaForge FreeCAD plugin — workbench entry point.

FreeCAD loads this via InitGui.py or Mod discovery.
All FreeCAD imports are lazy to allow unit testing without FreeCAD installed.
"""

# mypy: warn-unused-ignores = False
from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

GATEWAY_URL = os.environ.get("METAFORGE_GATEWAY_URL", "http://localhost:8000")

_chat_widget: Any = None
_document_observer: Any = None
_patch_server: Any = None


def initialize() -> None:
    """Initialize the MetaForge plugin. Called by FreeCAD on startup."""
    global _document_observer, _patch_server  # noqa: PLW0603
    from ide_assistants.cad_extension.freecad_plugin.document_observer import (
        DocumentObserver,
        PatchServer,
    )

    _document_observer = DocumentObserver()
    _patch_server = PatchServer()
    _patch_server.start()

    # Attach observer to the active document if one is open
    try:
        import FreeCAD  # type: ignore

        if FreeCAD.ActiveDocument:
            FreeCAD.ActiveDocument.addObserver(_document_observer)
    except Exception:
        pass

    logger.info("freecad_plugin_initialized", gateway_url=GATEWAY_URL)


def open_chat() -> None:
    """Open the MetaForge chat panel."""
    global _chat_widget  # noqa: PLW0603
    if _chat_widget is None:
        from ide_assistants.cad_extension.freecad_plugin.chat_widget import ChatWidget

        _chat_widget = ChatWidget(GATEWAY_URL)
    _chat_widget.show()


def shutdown() -> None:
    """Clean up on FreeCAD exit."""
    if _patch_server:
        _patch_server.stop()
    if _chat_widget:
        _chat_widget.hide()
    logger.info("freecad_plugin_shutdown")


# FreeCAD Workbench class (used when loaded as a Mod)
class MetaForgeWorkbench:
    """FreeCAD workbench providing the MetaForge menu and toolbar."""

    MenuText = "MetaForge"
    ToolTip = "MetaForge AI Engineering Assistant"

    def Initialize(self) -> None:  # noqa: N802
        try:
            import FreeCADGui  # type: ignore

            FreeCADGui.addCommand("MetaForge_OpenChat", _OpenChatCommand())
            self.list: list[str] = ["MetaForge_OpenChat"]
            self.appendToolbar("MetaForge", self.list)  # type: ignore[attr-defined]
            self.appendMenu("MetaForge", self.list)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("freecad_workbench_init_error", error=str(exc))

    def Activated(self) -> None:  # noqa: N802
        initialize()

    def Deactivated(self) -> None:  # noqa: N802
        shutdown()

    def GetClassName(self) -> str:  # noqa: N802
        return "Gui::PythonWorkbench"


class _OpenChatCommand:
    def GetResources(self) -> dict[str, str]:  # noqa: N802
        return {"MenuText": "Open MetaForge Assistant", "ToolTip": "Open MetaForge AI chat panel"}

    def Activated(self) -> None:  # noqa: N802
        open_chat()

    def IsActive(self) -> bool:  # noqa: N802
        return True


def register() -> None:
    """Register the workbench with FreeCAD. Called from InitGui.py."""
    try:
        import FreeCADGui  # type: ignore

        FreeCADGui.addWorkbench(MetaForgeWorkbench())
    except Exception as exc:
        logger.warning("freecad_plugin_register_error", error=str(exc))
