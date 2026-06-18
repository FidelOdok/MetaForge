"""FreeCAD AI adapter — fronts the FreeCAD AI authoring/kinematics toolset (MET-525/526)."""

from tool_registry.tools.freecad_ai.adapter import (
    FreecadAiServer,
    FreecadAiTransport,
    create_freecad_ai_server,
    http_transport,
)

__all__ = [
    "FreecadAiServer",
    "FreecadAiTransport",
    "create_freecad_ai_server",
    "http_transport",
]
