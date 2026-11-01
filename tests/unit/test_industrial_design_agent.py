"""The industrial design agent and its skills (FORGE-633)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from domain_agents.industrial_design import IndustrialDesignAgent, TaskRequest
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_context import cards_for_domains, load_skill_cards

REPO = Path(__file__).resolve().parents[2]
ID_SKILLS = {"form_factor_envelope", "check_ergonomics", "check_cmf_compatibility"}


@pytest.fixture
def agent() -> IndustrialDesignAgent:
    return IndustrialDesignAgent(twin=AsyncMock(), mcp=InMemoryMcpBridge())


@pytest.fixture(autouse=True)
def _no_llm() -> Any:
    with patch("domain_agents.industrial_design.agent.is_llm_available", return_value=False):
        yield


async def test_it_supports_its_three_skills(agent: IndustrialDesignAgent) -> None:
    assert agent.SUPPORTED_TASKS == ID_SKILLS
    out = await agent.run_task(TaskRequest(task_type="render"))
    assert not out.success and "Unsupported task type: render" in out.errors[0]


async def test_form_factor_envelope_runs_and_warns_on_a_misfit(
    agent: IndustrialDesignAgent,
) -> None:
    out = await agent.run_task(
        TaskRequest(
            task_type="form_factor_envelope",
            parameters={
                "components": [{"name": "battery", "dims_mm": [60, 35, 10]}],
                "stack_axis": "z",
                "clearance": {"value_mm": 1, "source_ref": "stated"},
                "wall_thickness": {"value_mm": 2, "source_ref": "fdm min wall"},
                "max_envelope": {"dims_mm": [60, 40, 15], "source_ref": "REQ-3"},
            },
        )
    )
    assert out.success
    assert out.skill_results[0]["outer_dims_mm"] == [66, 41, 16]
    assert out.warnings == ["the envelope exceeds the maximum"]


async def test_ergonomics_and_cmf_carry_their_unknowns_as_warnings(
    agent: IndustrialDesignAgent,
) -> None:
    ergo = await agent.run_task(
        TaskRequest(
            task_type="check_ergonomics",
            parameters={
                "product": "torch",
                "user_population": "adults",
                "features": [{"name": "barrel", "kind": "grip_diameter", "value": 28}],
            },
        )
    )
    assert ergo.success and ergo.skill_results[0]["verdict"] == "incomplete"
    assert "no sourced grip_diameter limit" in ergo.warnings[0]

    cmf = await agent.run_task(
        TaskRequest(
            task_type="check_cmf_compatibility",
            parameters={
                "parts": [{"name": "cap", "material": "abs", "appearance": "anodised_black"}],
                "appearances": [
                    {"name": "anodised_black", "finish": "anodised", "materials": ["aluminium"]}
                ],
            },
        )
    )
    assert cmf.success and cmf.warnings[0] == "verdict: incompatible"


async def test_invalid_parameters_are_refused(agent: IndustrialDesignAgent) -> None:
    out = await agent.run_task(TaskRequest(task_type="check_ergonomics", parameters={}))
    assert not out.success and out.errors[0].startswith("Invalid parameters")


def test_the_skills_are_discovered_under_the_industrial_design_domain() -> None:
    cards = load_skill_cards([str(REPO / "domain_agents")])
    names = {c.name for c in cards_for_domains(cards, ("industrial_design",))}
    assert names == ID_SKILLS
