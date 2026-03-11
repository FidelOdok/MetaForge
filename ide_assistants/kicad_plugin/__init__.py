"""MetaForge KiCad Plugin — chat assistant for PCB design.

Registers a KiCad pcbnew ActionPlugin that opens a Qt-based chat dialog
connected to the MetaForge gateway. Allows engineers to interact with
MetaForge agents directly from the KiCad PCB editor.

This plugin uses PyQt5/PySide (bundled with KiCad) and plain dataclasses
to avoid dependency conflicts with the KiCad Python environment.
"""

from __future__ import annotations

try:
    # When loaded inside KiCad, pcbnew is available.
    import pcbnew  # type: ignore[import-untyped]  # noqa: F401

    from .plugin import MetaForgeChatPlugin

    # Register the action plugin with KiCad's plugin system.
    MetaForgeChatPlugin().register()
except ImportError:
    # Outside of KiCad (e.g. during testing), pcbnew is not available.
    # Silently skip registration so that the module can still be imported.
    pass
