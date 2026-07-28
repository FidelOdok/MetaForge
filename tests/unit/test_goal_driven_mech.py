"""Goal-driven hybrid mechanical handler: guarantees a loadable cad_model (MET-10)."""

from __future__ import annotations

import pytest

from api_gateway.runs.mech_handlers import GoalDrivenMechanicalHandler, _normalize_spec
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow


def test_normalize_spec_defaults_and_coercion() -> None:
    s = _normalize_spec({}, "an aluminum camera bracket")
    assert s["kind"] == "box" and s["parameters"] and s["material"]
    assert s["name"]  # slugged from the goal, never empty
    # bad kind -> box; string dims coerced to float; unknown kind falls back
    s2 = _normalize_spec(
        {"name": "Arm", "kind": "weird", "parameters": {"length": "50"}, "material": "Steel"},
        "arm",
    )
    assert s2["kind"] == "box" and s2["parameters"]["length"] == 50.0 and s2["name"] == "Arm"


class _Bridge:
    """Fake MCP bridge that walks the author->export sequence."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append(tool)
        return {
            "freecad.open_session": {"status": "ok", "data": {"session_id": "s1"}},
            "freecad.create_primitive": {"status": "ok", "data": {"obj_id": "o1"}},
            "freecad.export_model": {"status": "ok", "data": {"step_base64": "U1RFUA=="}},
            "twin.record_decision": {"status": "ok", "data": {"id": "d1"}},
        }[tool]


@pytest.mark.asyncio
async def test_handler_authors_and_commits_loadable_cad() -> None:
    recorded: dict = {}

    async def recorder(**kwargs):
        recorded.update(kwargs)
        return {"node_id": "n1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_spec(
            {
                "name": "Camera Bracket",
                "kind": "box",
                "parameters": {"length": 40},
                "material": "Al6061",
            },
            goal,
        )

    bridge = _Bridge()
    handler = GoalDrivenMechanicalHandler(bridge, recorder, extract=fake_extract)
    phase = get_flow("mech_v1").phases[1]  # the design phase
    ctx = FlowContext(goal="an aluminum camera bracket", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert any(a.startswith("cad_model:n1") for a in outcome.artifacts)
    # the STEP bytes were committed through the recorder (loadable path)
    assert recorded["step_base64"] == "U1RFUA=="
    assert recorded["name"] == "Camera Bracket"
    # the full author->export->record sequence ran
    assert bridge.calls == [
        "freecad.open_session",
        "freecad.create_primitive",
        "freecad.export_model",
        "twin.record_decision",
    ]
