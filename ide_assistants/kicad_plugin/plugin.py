"""Main KiCad pcbnew ActionPlugin for MetaForge chat.

Inherits from ``pcbnew.ActionPlugin`` and opens the chat widget dialog
when the user triggers the action from the KiCad Tools menu.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MetaForgeChatPlugin:
    """KiCad ActionPlugin that opens the MetaForge chat dialog.

    This class is designed to work as a pcbnew.ActionPlugin subclass at
    runtime (inside KiCad) while remaining importable for testing without
    the ``pcbnew`` dependency.

    At import time the ``__init__.py`` calls ``register()`` which delegates
    to the real pcbnew registration machinery.
    """

    def __init__(self) -> None:
        self._name = "MetaForge Chat"
        self._category = "MetaForge"
        self._description = "Open the MetaForge AI assistant chat for PCB design guidance."
        self._show_toolbar_button = True
        self._icon_file_name = ""
        self._plugin_instance = None

    # ---- pcbnew.ActionPlugin interface ------------------------------------

    def defaults(self) -> None:
        """Set plugin metadata (called by KiCad during registration)."""
        self._name = "MetaForge Chat"
        self._category = "MetaForge"
        self._description = "Open the MetaForge AI assistant chat for PCB design guidance."

    def Run(self) -> None:  # noqa: N802 — KiCad uses PascalCase
        """Entry point invoked when the user activates the plugin."""
        try:
            from .chat_widget import ChatWidget

            widget = ChatWidget()
            widget.show()
        except Exception:
            logger.exception("Failed to open MetaForge chat widget")

    # ---- Registration helper ----------------------------------------------

    def register(self) -> None:
        """Register with pcbnew if available.

        Creates a proper ``pcbnew.ActionPlugin`` subclass at runtime and
        registers it.  This approach avoids inheriting from pcbnew at
        module level which would break imports outside of KiCad.
        """
        try:
            import pcbnew  # type: ignore[import-untyped]

            plugin_self = self

            class _Plugin(pcbnew.ActionPlugin):  # type: ignore[misc]
                def defaults(self) -> None:
                    self.name = plugin_self._name
                    self.category = plugin_self._category
                    self.description = plugin_self._description
                    self.show_toolbar_button = plugin_self._show_toolbar_button

                def Run(self) -> None:  # noqa: N802
                    plugin_self.Run()

            self._plugin_instance = _Plugin()
            self._plugin_instance.register()
            logger.info("MetaForge KiCad plugin registered")
        except ImportError:
            logger.debug("pcbnew not available — skipping plugin registration")
