"""The flow's proposal context reaches every phase on both engines (FORGE-491)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.converter import DataConverter

from api_gateway.runs.flow_brain import ReActPhaseBrain
from orchestrator.design_flow.context import (
    FlowContext as ProposalContext,
)
from orchestrator.design_flow.context import (
    ManufacturingContext,
    ManufacturingRoute,
    TargetMaturity,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.frozen import FrozenFlow, freeze_flow
from orchestrator.design_flow.spec import FLOWS, get_flow
from orchestrator.design_flow.temporal_flow import DesignFlowInput, PhaseRequest
from orchestrator.design_flow.versions import FlowVersionStore

PROPOSAL = ProposalContext(
    manufacturing=ManufacturingContext(
        route=ManufacturingRoute.IN_HOUSE,
        processes=("FDM printing",),
        machines=("Bambu X1C 256 mm cube",),
        stock_materials=("18 mm birch ply 800 x 300",),
        production_quantity=1,
    ),
    target_maturity=TargetMaturity.SIM_VALIDATED,
    loads_and_use="15 kg books plus a 10 kg front-edge point load, studs at 400 mm",
    budget="under 20 GBP",
    requirements=("must fit a 800 mm wall run",),
)
BLOCK = PROPOSAL.render_for_phases()


def test_render_is_deterministic_and_carries_every_stated_value() -> None:
    assert BLOCK == PROPOSAL.render_for_phases()
    for needle in ("18 mm birch ply", "10 kg front-edge", "400 mm", "20 GBP", "sim_validated"):
        assert needle in BLOCK
    assert "must fit a 800 mm wall run" in BLOCK


def test_empty_context_renders_nothing() -> None:
    assert ProposalContext().render_for_phases() == ""


def test_context_is_part_of_the_content_hash() -> None:
    flow = get_flow("hardware_v1")
    plain = freeze_flow(flow)
    with_ctx = freeze_flow(flow, context=BLOCK)
    other = freeze_flow(flow, context=BLOCK + " changed")
    assert len({plain.content_hash, with_ctx.content_hash, other.content_hash}) == 3
    with_ctx.verify()
    with_ctx.context = "tampered"
    with pytest.raises(ValueError, match="does not match"):
        with_ctx.verify()


def test_hash_without_context_is_unchanged_from_before() -> None:
    flow = get_flow("hardware_v1")
    frozen = freeze_flow(flow)
    assert frozen.context == ""
    assert frozen.compute_hash() == freeze_flow(flow, context="").content_hash


def test_context_survives_save_and_restart(tmp_path: Path) -> None:
    flow = next(iter(FLOWS.values()))
    db = str(tmp_path / "v.db")
    v = FlowVersionStore(db).save(
        flow, base_template_id=flow.id, base_version="1.0.0", changes=[], context=BLOCK
    )
    again = FlowVersionStore(db).get(v.id)
    assert again.frozen.context == BLOCK
    assert again.frozen.content_hash == v.frozen.content_hash
    again.frozen.verify()


def test_old_database_without_the_column_still_loads(tmp_path: Path) -> None:
    import sqlite3

    flow = next(iter(FLOWS.values()))
    db = str(tmp_path / "v.db")
    v = FlowVersionStore(db).save(flow, base_template_id=flow.id, base_version="1.0.0", changes=[])
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE flow_versions DROP COLUMN flow_context")
    conn.commit()
    conn.close()
    again = FlowVersionStore(db).get(v.id)
    assert again.frozen.context == ""
    assert again.frozen.content_hash == v.frozen.content_hash


def test_temporal_payload_round_trip_keeps_context() -> None:
    conv = DataConverter.default.payload_converter
    frozen = freeze_flow(get_flow("hardware_v1"), context=BLOCK)
    inp = DesignFlowInput(run_id="r1", goal="a shelf", flow=frozen)
    back = conv.from_payloads(conv.to_payloads([inp]), [DesignFlowInput])[0]
    assert back.flow.context == BLOCK
    back.flow.verify()

    req = PhaseRequest(run_id="r1", goal="g", phase=frozen.phases[0], flow_context=BLOCK)
    assert conv.from_payloads(conv.to_payloads([req]), [PhaseRequest])[0].flow_context == BLOCK


def test_in_flight_payload_without_context_still_decodes() -> None:
    conv = DataConverter.default.payload_converter
    frozen = freeze_flow(get_flow("hardware_v1"))
    legacy = json.loads(
        conv.to_payloads([DesignFlowInput(run_id="r", goal="g", flow=frozen)])[0].data
    )
    legacy["flow"].pop("context")
    payload = conv.to_payloads([legacy])
    back = conv.from_payloads(payload, [DesignFlowInput])[0]
    assert back.flow.context == ""
    assert isinstance(back.flow, FrozenFlow)
    back.flow.verify()


def _prompt(phase_id: str, flow_context: str) -> str:
    brain = ReActPhaseBrain.__new__(ReActPhaseBrain)
    brain._cards = []
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == phase_id)
    ctx = FlowContext(goal="a shelf", project_id="p1", flow_context=flow_context)
    return brain._prompt("a shelf", phase, ctx)


def test_phase_prompt_leads_with_the_same_context_block_for_every_phase() -> None:
    first = _prompt("intent", BLOCK)
    later = _prompt("needs", BLOCK)
    assert "18 mm birch ply" in first
    assert first.index("18 mm birch ply") < first.index("Product goal")
    prefix = first.split("You are MetaForge")[0]
    assert later.startswith(prefix)
    assert prefix.strip()


def test_phase_prompt_without_context_is_unchanged() -> None:
    assert _prompt("intent", "").startswith("You are MetaForge")


@pytest.mark.asyncio
async def test_worker_hands_the_request_context_to_the_brain(monkeypatch) -> None:
    from api_gateway.runs import flow_worker

    seen: dict[str, str] = {}

    class _Brain:
        async def run_phase(self, *, goal, phase, context):
            seen["ctx"] = context.flow_context
            from orchestrator.design_flow.executor import PhaseOutcome

            return PhaseOutcome(summary="ok", artifacts=[], status="completed")

    async def _bridge():
        return None

    async def _build(run_id, flow_id):
        return _Brain()

    monkeypatch.setattr(flow_worker, "ensure_mcp_bridge", _bridge)
    monkeypatch.setattr(flow_worker, "_log_phase_skills", lambda r: None)
    monkeypatch.setattr("api_gateway.runs.routes.build_phase_brain", _build)
    frozen = freeze_flow(get_flow("hardware_v1"), context=BLOCK)
    req = PhaseRequest(
        run_id="r1",
        goal="g",
        phase=frozen.phases[0],
        flow_id="hardware_v1",
        flow_context=frozen.context,
    )
    await flow_worker._run_phase(req)
    assert seen["ctx"] == BLOCK


@pytest.mark.asyncio
async def test_in_process_executor_gives_every_phase_the_context() -> None:
    from orchestrator.design_flow.executor import DesignFlowExecutor, GateCoordinator, PhaseOutcome
    from orchestrator.design_flow.spec import FlowDefinition, Phase
    from orchestrator.harness.runs import InMemoryRunStore

    seen: list[str] = []

    class _Brain:
        async def run_phase(self, *, goal, phase, context):
            seen.append(context.flow_context)
            return PhaseOutcome(summary="ok")

    flow = FlowDefinition(
        id="t",
        name="t",
        phases=(
            Phase(id="a", title="A", objective="o", enforce_deliverables=False),
            Phase(id="b", title="B", objective="o", enforce_deliverables=False),
        ),
    )
    store = InMemoryRunStore()
    run = store.create({"flow": "t", "goal": "a shelf"})
    ex = DesignFlowExecutor(store=store, brain=_Brain(), coordinator=GateCoordinator())
    await ex.run(run.id, flow, flow_context=BLOCK)
    assert seen == [BLOCK, BLOCK]
