"""Runs MCP adapter — chat-triggered design flows (MET-587).

- ``run.start_design_flow`` — start a gated design lifecycle for a goal
- ``run.get_status`` — check a run's status / gate reason
"""

from tool_registry.tools.runs.adapter import RunsServer

__all__ = ["RunsServer"]
