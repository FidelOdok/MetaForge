"""MetaForge top-level package — entrypoints, currently for the MCP server.

The platform's library code lives under flat sub-packages (``twin_core``,
``mcp_core``, ``tool_registry``, etc.). This package only houses
deployment / process entrypoints that need a stable
``python -m metaforge.<thing>`` invocation surface.
"""

__all__: list[str] = []
