"""Temporal activity wrappers for MetaForge domain agents.

Each activity wraps a domain agent's ``run_task()`` method, adding crash
recovery, retry semantics, and observability via Temporal's activity
infrastructure.
"""

from orchestrator.activities.approval_activity import wait_for_approval
from orchestrator.activities.base_activity import (
    AgentActivityInput,
    AgentActivityOutput,
    ApprovalRequest,
    ApprovalResult,
    get_default_retry_policy,
)
from orchestrator.activities.electronics_activity import run_electronics_agent
from orchestrator.activities.firmware_activity import run_firmware_agent
from orchestrator.activities.mechanical_activity import run_mechanical_agent
from orchestrator.activities.simulation_activity import run_simulation_agent

__all__ = [
    # Base models
    "AgentActivityInput",
    "AgentActivityOutput",
    "ApprovalRequest",
    "ApprovalResult",
    "get_default_retry_policy",
    # Activities
    "run_mechanical_agent",
    "run_electronics_agent",
    "run_firmware_agent",
    "run_simulation_agent",
    "wait_for_approval",
]
