"""The flow generator asks instead of guessing (FORGE-463).

The bug: ``POST /v1/design-flows/propose`` with only ``{"intent": "A wall
shelf I can make with what I have."}`` came back as a ``mech_v1`` proposal
that dropped simulation ("practical load testing will suffice"), asked
nothing, recorded no assumptions, and reported ``valid: true`` -- because no
requirements existed yet and "every requirement is verified" is trivially
true of none.

Three properties are protected here:

* **intent-only asks.** Missing route, maturity or loads gives
  ``needs_input`` with MetaForge's own questions, and creates nothing: no
  flow, no stored version, no held approval.
* **full context is used.** The prompt carries it, every change records the
  capabilities it was made under, and an undecided route becomes a gated
  decision rather than a guess.
* **verification is not dropped blind.** Dropping simulation with unknown
  loads, or with no recorded alternative, is a named violation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orchestrator.design_flow.context import (
    MAX_EXTRA_QUESTIONS,
    FlowContext,
    ManufacturingContext,
    ManufacturingRoute,
    TargetMaturity,
    missing_inputs,
)
from orchestrator.design_flow.generator import (
    ROUTE_SELECTION_PHASE_ID,
    Operation,
    OperationKind,
    build_proposal,
    parse_operations,
)
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.spec import get_flow
from orchestrator.design_flow.templates import load_templates

_SHELF = "A wall shelf I can make with what I have."

_FULL = {
    "manufacturingContext": {
        "route": "in_house",
        "processes": ["woodworking"],
        "machines": ["table saw, 600 mm rip capacity", "cordless drill"],
        "stockMaterials": ["18 mm birch plywood"],
        "productionQuantity": 1,
    },
    "targetMaturity": "physically_validated",
    "loadsAndUse": "20 kg of books, indoors, screwed into two wall studs",
}


def _version(flow_id: str) -> str:
    return load_templates()[flow_id].version


def _context(**overrides: Any) -> FlowContext:
    values: dict[str, Any] = {
        "manufacturing": ManufacturingContext(
            route=ManufacturingRoute.IN_HOUSE,
            processes=("woodworking",),
            machines=("table saw, 600 mm rip capacity",),
            stock_materials=("18 mm birch plywood",),
        ),
        "target_maturity": TargetMaturity.PHYSICALLY_VALIDATED,
        "loads_and_use": "20 kg of books",
    }
    values.update(overrides)
    return FlowContext(**values)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from api_gateway.server import create_app

    return TestClient(create_app())


@pytest.fixture
def no_extra_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.design_flows.generate as gen

    async def unavailable(*args: Any, **kwargs: Any) -> list[Any]:
        raise gen.GeneratorUnavailableError("connection refused")

    monkeypatch.setattr(gen, "suggest_extra_questions", unavailable)


@pytest.fixture
def generator_must_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.design_flows.generate as gen

    async def boom(request: Any) -> Any:
        raise AssertionError("the generator ran on an incomplete request")

    monkeypatch.setattr(gen, "generate_proposal", boom)


def _model_replies(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> list[str]:
    """Stand in for the model, and keep the prompts it was sent."""
    import api_gateway.chat.harness_backend as backend

    prompts: list[str] = []

    async def fake_turn(prompt: str, **kwargs: Any) -> str:
        prompts.append(prompt)
        return json.dumps(reply)

    monkeypatch.setattr(backend, "run_chat_turn", fake_turn)
    return prompts


# ── intent only: ask, create nothing ─────────────────────────────────────


class TestIntentOnlyAsks:
    def test_the_bug_request_needs_input_with_the_required_questions(
        self, client: TestClient, no_extra_questions: None, generator_must_not_run: None
    ) -> None:
        from api_gateway.chat.tool_approvals import get_approval_store
        from orchestrator.design_flow.versions import get_version_store

        approvals_before = len(get_approval_store().list())
        versions_before = len(get_version_store().list())

        response = client.post("/v1/design-flows/propose", json={"intent": _SHELF})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "needs_input"
        assert [q["id"] for q in body["questions"]] == [
            "manufacturing_route",
            "target_maturity",
            "loads_and_use",
        ]
        for q in body["questions"]:
            assert q["source"] == "metaforge"
            assert q["required"] is True
            assert q["why"]
        route_q = body["questions"][0]
        assert route_q["answerType"] == "choice"
        assert route_q["options"] == ["in_house", "vendor", "undecided"]

        # Nothing was generated, stored or held.
        assert "approvalId" not in body
        assert "flow" not in body
        assert len(get_approval_store().list()) == approvals_before
        assert len(get_version_store().list()) == versions_before

    def test_an_unreachable_model_is_said_not_swallowed(
        self, client: TestClient, no_extra_questions: None, generator_must_not_run: None
    ) -> None:
        body = client.post("/v1/design-flows/propose", json={"intent": _SHELF}).json()
        assert any("could not be generated" in n for n in body["notes"])

    def test_the_model_may_add_a_few_product_specific_questions(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, generator_must_not_run: None
    ) -> None:
        extras = [
            {"id": f"q{i}", "question": f"Extra {i}?", "why": "changes the flow"}
            for i in range(MAX_EXTRA_QUESTIONS + 3)
        ]
        _model_replies(monkeypatch, {"questions": extras})
        body = client.post("/v1/design-flows/propose", json={"intent": _SHELF}).json()

        model_qs = [q for q in body["questions"] if q["source"] == "model"]
        assert len(model_qs) == MAX_EXTRA_QUESTIONS
        assert all(q["required"] is False for q in model_qs)
        # MetaForge's own come first, whatever the model said.
        assert body["questions"][0]["id"] == "manufacturing_route"

    def test_only_what_is_missing_is_asked(
        self, client: TestClient, no_extra_questions: None, generator_must_not_run: None
    ) -> None:
        body = client.post(
            "/v1/design-flows/propose",
            json={"intent": _SHELF, "targetMaturity": "concept", "loadsAndUse": "unknown"},
        ).json()
        assert [q["id"] for q in body["questions"]] == ["manufacturing_route"]

    def test_in_house_with_no_capabilities_asks_what_they_are(self) -> None:
        """'with what I have' is the bug's own wording: an in-house route with
        no stated tools is still a guess about what they are."""
        context = _context(manufacturing=ManufacturingContext(route=ManufacturingRoute.IN_HOUSE))
        assert [q.id for q in missing_inputs(context)] == ["in_house_capabilities"]

    def test_snake_case_is_accepted(
        self, client: TestClient, no_extra_questions: None, generator_must_not_run: None
    ) -> None:
        body = client.post(
            "/v1/design-flows/propose",
            json={
                "intent": _SHELF,
                "manufacturing_context": {"route": "vendor"},
                "target_maturity": "concept",
            },
        ).json()
        assert [q["id"] for q in body["questions"]] == ["loads_and_use"]

    def test_an_unknown_route_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/v1/design-flows/propose",
            json={"intent": _SHELF, "manufacturingContext": {"route": "magic"}},
        )
        assert response.status_code == 422


# ── full context: used, and visible in the proposal ──────────────────────


class TestFullContextIsUsed:
    def test_a_valid_proposal_whose_rationale_cites_the_capabilities(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts = _model_replies(
            monkeypatch,
            {
                "template": "mech_v1",
                "rationale": "a single load-bearing part",
                "operations": [
                    {
                        "op": "add_deliverable",
                        "phase": "simulation",
                        "value": "test_plan",
                        "rationale": "physically validated: a load test on the built shelf",
                    },
                    {
                        "op": "set_disciplines",
                        "phase": "design",
                        "value": ["mechanical"],
                        "rationale": "sized to 18 mm plywood cut on the table saw",
                    },
                ],
                "assumptions": ["the studs are 400 mm apart"],
            },
        )
        response = client.post("/v1/design-flows/propose", json={"intent": _SHELF, **_FULL})
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["status"] == "proposed"
        assert body["valid"] is True, body["violations"]
        assert body["approvalId"] and body["versionId"]
        assert body["changes"]
        for change in body["changes"]:
            assert "table saw, 600 mm rip capacity" in change["basis"]
            assert "18 mm birch plywood" in change["basis"]
        assert "the studs are 400 mm apart" in body["assumptions"]
        # No requirements were sent: said, not passed over.
        assert body["requirementsPending"] is True
        assert any("requirements pending" in a for a in body["assumptions"])

        # And the model was told what the person has.
        prompt = prompts[-1]
        assert "table saw, 600 mm rip capacity" in prompt
        assert "18 mm birch plywood" in prompt
        assert "physically_validated" in prompt

    def test_the_diff_a_reviewer_reads_carries_the_basis(self) -> None:
        proposal = build_proposal(
            get_flow("mech_v1"),
            base_version=_version("mech_v1"),
            operations=[
                Operation(OperationKind.SET_DISCIPLINES, "design", "plywood", value=["mechanical"])
            ],
            context=_context(),
        )
        assert "table saw" in proposal.diff()[0]

    def test_an_undecided_route_becomes_a_gated_decision(self) -> None:
        context = _context(manufacturing=ManufacturingContext(route=ManufacturingRoute.UNDECIDED))
        proposal = build_proposal(
            get_flow("mech_v1"), base_version=_version("mech_v1"), operations=[], context=context
        )
        ids = [p.id for p in proposal.definition.phases]
        assert ROUTE_SELECTION_PHASE_ID in ids
        assert ids.index(ROUTE_SELECTION_PHASE_ID) == ids.index("design") - 1
        phase = proposal.definition.phases[ids.index(ROUTE_SELECTION_PHASE_ID)]
        assert phase.gate is not None and not phase.gate.auto_approve
        assert phase.required_deliverables
        assert proposal.operations[-1].kind is OperationKind.ADD_ROUTE_SELECTION
        assert proposal.valid, [str(v) for v in proposal.validation.violations]

    def test_a_model_cannot_add_the_route_phase_itself(self) -> None:
        ops = parse_operations(
            [{"op": "add_route_selection", "phase": "route_selection", "rationale": "x"}]
        )
        assert ops == []


# ── verification is not dropped blind ────────────────────────────────────


_DROP_SIM = Operation(OperationKind.DROP_PHASE, "simulation", "practical load testing will suffice")


class TestVerificationIsNotDroppedBlind:
    def test_the_bug_proposal_is_no_longer_valid(self) -> None:
        """The exact FORGE-463 output: mech_v1 minus simulation, no context."""
        proposal = build_proposal(
            get_flow("mech_v1"), base_version=_version("mech_v1"), operations=[_DROP_SIM]
        )
        assert proposal.valid is False
        rules = {v.rule for v in proposal.validation.violations}
        assert "physical-verification-kept" in rules
        assert "requirements-are-verified" in rules

    def test_dropping_simulation_with_unknown_loads_is_a_violation(self) -> None:
        proposal = build_proposal(
            get_flow("mech_v1"),
            base_version=_version("mech_v1"),
            operations=[
                _DROP_SIM,
                Operation(
                    OperationKind.ADD_DELIVERABLE, "design", "a test instead", value="test_plan"
                ),
            ],
            context=_context(loads_and_use="unknown"),
        )
        assert proposal.valid is False
        messages = [str(v) for v in proposal.validation.violations]
        assert any("physical-verification-kept" in m and "loads are unknown" in m for m in messages)

    def test_known_loads_and_a_gated_test_plan_may_replace_simulation(self) -> None:
        proposal = build_proposal(
            get_flow("mech_v1"),
            base_version=_version("mech_v1"),
            operations=[
                _DROP_SIM,
                Operation(
                    OperationKind.ADD_DELIVERABLE,
                    "design",
                    "a 20 kg load test on the built shelf",
                    value="test_plan",
                ),
            ],
            context=_context(),
        )
        assert proposal.valid, [str(v) for v in proposal.validation.violations]

    def test_known_loads_without_an_alternative_is_still_a_violation(self) -> None:
        proposal = build_proposal(
            get_flow("mech_v1"),
            base_version=_version("mech_v1"),
            operations=[_DROP_SIM],
            context=_context(),
        )
        assert "physical-verification-kept" in {v.rule for v in proposal.validation.violations}

    def test_every_shipping_template_still_passes(self) -> None:
        for flow_id in load_templates():
            assert validate_flow(get_flow(flow_id), context=_context(loads_and_use="unknown")).ok

    def test_the_route_refuses_it_before_storing_anything(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from orchestrator.design_flow.versions import get_version_store

        _model_replies(
            monkeypatch,
            {
                "template": "mech_v1",
                "operations": [
                    {
                        "op": "drop_phase",
                        "phase": "simulation",
                        "rationale": "practical load testing will suffice",
                    }
                ],
            },
        )
        before = len(get_version_store().list())
        response = client.post(
            "/v1/design-flows/propose", json={"intent": _SHELF, **_FULL, "loadsAndUse": "unknown"}
        )
        assert response.status_code == 422
        assert "physical-verification-kept" in response.json()["detail"]
        assert len(get_version_store().list()) == before


# ── over MCP ─────────────────────────────────────────────────────────────


class TestOverMcp:
    async def test_intent_only_tells_the_agent_to_ask_the_user(
        self, no_extra_questions: None, generator_must_not_run: None
    ) -> None:
        from api_gateway.design_flows.mcp_bindings import make_proposer
        from tool_registry.tools.design_flow.adapter import DesignFlowServer

        server = DesignFlowServer(proposer=make_proposer())
        result = await server.propose({"intent": _SHELF})
        assert result["status"] == "needs_input"
        assert [q["id"] for q in result["questions"]] == [
            "manufacturing_route",
            "target_maturity",
            "loads_and_use",
        ]
        assert "approval_id" not in result
        assert "Ask the user" in result["next_step"]

    def test_the_schema_carries_the_context_fields(self) -> None:
        from tool_registry.tools.design_flow.adapter import DesignFlowServer

        async def _p(**kwargs: Any) -> dict[str, Any]:
            return {}

        server = DesignFlowServer(proposer=_p)
        props = server._tools["flow.propose"].manifest.input_schema["properties"]
        for key in ("manufacturing_context", "target_maturity", "loads_and_use", "budget"):
            assert key in props
        assert props["manufacturing_context"]["properties"]["route"]["enum"] == [
            "in_house",
            "vendor",
            "undecided",
        ]

    async def test_the_context_reaches_the_binding(self) -> None:
        from tool_registry.tools.design_flow.adapter import DesignFlowServer

        seen: dict[str, Any] = {}

        async def _p(**kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {"status": "needs_input"}

        await DesignFlowServer(proposer=_p).propose(
            {
                "intent": "shelf",
                "manufacturing_context": {"route": "undecided"},
                "target_maturity": "concept",
                "loads_and_use": "unknown",
                "budget": "50 EUR",
            }
        )
        assert seen["manufacturing_context"] == {"route": "undecided"}
        assert seen["target_maturity"] == "concept"
        assert seen["loads_and_use"] == "unknown"
        assert seen["budget"] == "50 EUR"
