"""The gateway serves the flows it actually runs (FORGE-395).

``dashboard/src/api/endpoints/design-flows.ts`` used to *be* the catalogue: a
hand-maintained copy of ``spec.py`` under a comment reading "Keep this list in
sync when a flow is added or re-phased". It was not in sync, and nothing could
have said so.

The headline test here is the ticket's own acceptance criterion: a flow added
on the server appears in what the wizard reads, with no dashboard change. That
is the property the comment was asking humans to maintain by hand.
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FLOWS


@pytest.fixture(scope="module")
def client() -> TestClient:
    from api_gateway.server import create_app

    return TestClient(create_app())


class TestTheCatalogueIsServed:
    def test_every_flow_the_gateway_runs_is_listed(self, client: TestClient) -> None:
        body = client.get("/v1/design-flows").json()
        assert sorted(f["id"] for f in body["flows"]) == sorted(FLOWS)

    def test_the_default_flow_is_named_and_present(self, client: TestClient) -> None:
        """The concrete drift this ticket found.

        ``design_v1`` is the flow a run gets when the request names none --
        and it was absent from the hand-copied list entirely, so the default
        could not be chosen in the wizard that exists to choose flows.
        """
        body = client.get("/v1/design-flows").json()
        assert body["defaultFlowId"] == DEFAULT_FLOW_ID
        listed = {f["id"]: f for f in body["flows"]}
        assert DEFAULT_FLOW_ID in listed
        assert listed[DEFAULT_FLOW_ID]["isDefault"] is True
        assert sum(1 for f in body["flows"] if f["isDefault"]) == 1

    def test_phase_titles_are_the_real_ones(self, client: TestClient) -> None:
        """The other half of the drift: four titles were paraphrased, so the
        wizard described phases by names no run ever uses."""
        body = client.get("/v1/design-flows").json()
        served = {f["id"]: [p["title"] for p in f["phases"]] for f in body["flows"]}
        expected = {fid: [p.title for p in flow.phases] for fid, flow in FLOWS.items()}
        assert served == expected

    @pytest.mark.parametrize("field", ["label", "description"])
    def test_presentation_text_is_served_not_held_by_the_client(
        self, client: TestClient, field: str
    ) -> None:
        """The short names lived only in the TypeScript.

        Serving `name` alone would have moved the drift rather than removed
        it: the dashboard would still have had to hold a friendly label for
        each flow, and that copy would rot the same way.
        """
        for flow in client.get("/v1/design-flows").json()["flows"]:
            assert flow[field].strip(), f"{flow['id']} has no {field}"

    def test_gate_criteria_and_deliverables_are_included(self, client: TestClient) -> None:
        """Things the old copy could not carry at all.

        The wizard needs them to say what a flow will demand, and the plan
        canvas (FORGE-399) needs them to render a gate as more than a name.
        """
        hardware = client.get("/v1/design-flows/hardware_v1").json()
        requirements = next(p for p in hardware["phases"] if p["id"] == "requirements")
        assert requirements["requiredDeliverables"]
        assert requirements["gate"]["criteria"]

    def test_a_version_comes_with_every_flow(self, client: TestClient) -> None:
        for flow in client.get("/v1/design-flows").json()["flows"]:
            assert flow["version"]

    def test_one_flow_can_be_fetched(self, client: TestClient) -> None:
        body = client.get("/v1/design-flows/mech_v1").json()
        assert body["id"] == "mech_v1"

    def test_an_unknown_flow_is_a_404_naming_the_known_ones(self, client: TestClient) -> None:
        response = client.get("/v1/design-flows/nope")
        assert response.status_code == 404
        assert "hardware_v1" in response.json()["detail"]


class TestValidityIsServedNotAssumed:
    def test_every_shipping_flow_reports_valid(self, client: TestClient) -> None:
        for flow in client.get("/v1/design-flows").json()["flows"]:
            assert flow["valid"] is True, flow["violations"]

    def test_an_invalid_flow_is_listed_as_unstartable_rather_than_hidden(
        self, client: TestClient, tmp_path, monkeypatch
    ) -> None:
        """A flow that cannot pass its own rules must not be offered as
        startable and then refused at ``POST /v1/runs``.

        That reads as the gateway being broken rather than the flow being
        wrong, and the person choosing has no way to tell which. Hiding it
        is no better: a flow that exists and cannot be seen is a support
        question nobody can answer.
        """
        import api_gateway.design_flows.routes as routes

        broken = {
            "id": "broken_v1",
            "version": "0.1.0",
            "label": "Broken",
            "description": "Deliberately invalid.",
            "name": "Broken flow",
            # A gate with no required deliverables, and no release gate:
            # two rules, so the response must carry both.
            "phases": [
                {
                    "id": "only",
                    "title": "Only",
                    "objective": "x",
                    "gate": {"name": "Check"},
                }
            ],
        }
        (tmp_path / "broken_v1.yaml").write_text(yaml.dump(broken))

        import orchestrator.design_flow.templates as templates_mod

        monkeypatch.setattr(templates_mod, "TEMPLATE_DIR", tmp_path)
        templates_mod.load_templates.cache_clear()
        monkeypatch.setattr(routes, "FLOWS", {"broken_v1": None})
        try:
            view = routes._to_view("broken_v1")
        finally:
            templates_mod.load_templates.cache_clear()

        assert view.valid is False
        assert len(view.violations) >= 2
        assert any("no-pass-without-data" in v for v in view.violations)


class TestTheAcceptanceCriterion:
    def test_a_new_flow_appears_without_touching_the_dashboard(self, tmp_path, monkeypatch) -> None:
        """The ticket's own acceptance criterion, and the whole point.

        A flow dropped into the template directory is served. Nothing in
        ``dashboard/`` is edited, because there is no longer a list there to
        edit -- which is exactly the maintenance the old comment was asking a
        human to remember.
        """
        import api_gateway.design_flows.routes as routes
        import orchestrator.design_flow.templates as templates_mod

        # Copy the real templates, then add one.
        for existing in templates_mod.TEMPLATE_DIR.glob("*.yaml"):
            (tmp_path / existing.name).write_text(existing.read_text())
        (tmp_path / "thermal_v1.yaml").write_text(
            yaml.dump(
                {
                    "id": "thermal_v1",
                    "version": "1.0.0",
                    "label": "Thermal study",
                    "description": "A thermal-only flow.",
                    "name": "Thermal study",
                    "phases": [
                        {
                            "id": "requirements",
                            "title": "Requirements",
                            "objective": "capture thermal limits",
                            "expected_artifacts": ["constraint_set"],
                            "required_deliverables": ["constraint_set"],
                            "gate": {"name": "Requirements sign-off"},
                        },
                        {
                            "id": "verify",
                            "title": "Thermal V&V",
                            "objective": "simulate",
                            "expected_artifacts": ["simulation_result"],
                            "required_deliverables": ["simulation_result"],
                            "gate": {"name": "Release sign-off"},
                        },
                    ],
                }
            )
        )
        monkeypatch.setattr(templates_mod, "TEMPLATE_DIR", tmp_path)
        templates_mod.load_templates.cache_clear()
        try:
            loaded = templates_mod.load_templates()
            monkeypatch.setattr(routes, "FLOWS", dict.fromkeys(loaded))
            view = routes._to_view("thermal_v1")
            listed = routes.list_design_flows()
        finally:
            templates_mod.load_templates.cache_clear()

        assert "thermal_v1" in {f.id for f in listed.flows}
        assert view.label == "Thermal study"
        assert [p.title for p in view.phases] == ["Requirements", "Thermal V&V"]
        # And it had to pass the invariants to be startable -- a new flow does
        # not get to skip the rules just because it is new.
        assert view.valid is True


# ── the proposal is a held write (FORGE-398) ─────────────────────────────

#: Enough context that the proposal does not stop to ask (FORGE-463).
_CONTEXT = {
    "manufacturingContext": {
        "route": "in_house",
        "processes": ["woodworking"],
        "machines": ["table saw, 600 mm rip capacity"],
        "stockMaterials": ["18 mm birch plywood"],
    },
    "targetMaturity": "physically_validated",
    "loadsAndUse": "40 kg of dishes per shelf, indoors",
}


class TestProposeHoldsForAHuman:
    def test_a_proposal_creates_an_approval_and_not_a_run(
        self, client: TestClient, monkeypatch
    ) -> None:
        """The rule is "nothing starts before approval", and the way it is
        made true is that this endpoint has no ability to start anything.

        Asserted by counting runs: a proposal must leave the run store as it
        found it. An endpoint that created a run and marked it pending would
        satisfy a looser reading and be one bug away from starting it.
        """
        import api_gateway.design_flows.routes as routes
        from api_gateway.runs.routes import get_run_store
        from orchestrator.design_flow.generator import Operation, OperationKind, build_proposal
        from orchestrator.design_flow.spec import get_flow
        from orchestrator.design_flow.templates import load_templates

        async def fake_generate(request):
            return build_proposal(
                get_flow("hardware_v1"),
                base_version=load_templates()["hardware_v1"].version,
                operations=[
                    Operation(OperationKind.DROP_PHASE, "firmware", "no firmware in a cabinet")
                ],
                intent=request.intent,
            )

        import api_gateway.design_flows.generate as gen

        monkeypatch.setattr(gen, "generate_proposal", fake_generate)

        runs_before = len(get_run_store().list())
        response = client.post(
            "/v1/design-flows/propose", json={"intent": "a kitchen cabinet", **_CONTEXT}
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["approvalId"]
        assert len(get_run_store().list()) == runs_before, "a proposal created a run"

        # The approval is in the queue a human is already watching.
        pending = client.get("/v1/chat/tool_approvals").json()["runs"]
        assert any(r["id"] == body["approvalId"] for r in pending)

        del routes  # imported for the monkeypatch target's module identity

    def test_the_proposal_shows_what_changed_and_why(self, client: TestClient, monkeypatch) -> None:
        import api_gateway.design_flows.generate as gen
        from orchestrator.design_flow.generator import Operation, OperationKind, build_proposal
        from orchestrator.design_flow.spec import get_flow
        from orchestrator.design_flow.templates import load_templates

        async def fake_generate(request):
            return build_proposal(
                get_flow("hardware_v1"),
                base_version=load_templates()["hardware_v1"].version,
                operations=[
                    Operation(OperationKind.DROP_PHASE, "firmware", "no firmware in a cabinet")
                ],
                intent=request.intent,
            )

        monkeypatch.setattr(gen, "generate_proposal", fake_generate)
        body = client.post(
            "/v1/design-flows/propose", json={"intent": "a kitchen cabinet", **_CONTEXT}
        ).json()

        assert body["baseTemplateId"] == "hardware_v1"
        assert body["baseVersion"]
        assert body["changes"] == [
            {
                "op": "drop_phase",
                "phase": "firmware",
                "value": None,
                "rationale": "no firmware in a cabinet",
                "basis": "",
            }
        ]
        assert "firmware" not in {p["id"] for p in body["flow"]["phases"]}
        assert body["valid"] is True

    def test_an_empty_intent_is_refused(self, client: TestClient) -> None:
        assert client.post("/v1/design-flows/propose", json={"intent": "  "}).status_code == 400

    def test_no_model_means_no_proposal_rather_than_an_untailored_one(
        self, client: TestClient, monkeypatch
    ) -> None:
        """A flow the human believes was tailored, and was not, is worse than
        being told the generator is down -- they would approve it on the
        strength of a tailoring that never happened."""
        import api_gateway.design_flows.generate as gen

        async def unavailable(request):
            raise gen.GeneratorUnavailableError("connection refused")

        monkeypatch.setattr(gen, "generate_proposal", unavailable)
        response = client.post("/v1/design-flows/propose", json={"intent": "a drone", **_CONTEXT})
        assert response.status_code == 503
        assert "no untailored fallback" in response.json()["detail"]


# ── editing a flow (FORGE-399) ───────────────────────────────────────────


def _phase_payload(phase) -> dict:
    return {
        "id": phase.id,
        "title": phase.title,
        "objective": phase.objective,
        "expectedArtifacts": list(phase.expected_artifacts),
        "requiredDeliverables": list(phase.required_deliverables),
        "enforceDeliverables": phase.enforce_deliverables,
        "disciplines": list(phase.disciplines),
        "gate": (
            None
            if phase.gate is None
            else {
                "name": phase.gate.name,
                "autoApprove": phase.gate.auto_approve,
                "criteria": list(phase.gate.criteria),
                "enforceConstraints": phase.gate.enforce_constraints,
                "gateId": phase.gate.gate_id,
            }
        ),
    }


def _edit_body(drop: str | None = None) -> dict:
    from orchestrator.design_flow.spec import get_flow

    phases = [_phase_payload(p) for p in get_flow("hardware_v1").phases if p.id != drop]
    return {"baseTemplateId": "hardware_v1", "phases": phases}


class TestLiveValidation:
    def test_a_sound_edit_validates(self, client: TestClient) -> None:
        body = client.post("/v1/design-flows/validate", json=_edit_body(drop="firmware")).json()
        assert body["valid"] is True
        assert body["violations"] == []

    def test_a_broken_edit_names_the_rule_while_you_are_looking_at_it(
        self, client: TestClient
    ) -> None:
        """Validation happens as the canvas changes, not at save -- by save
        time a person has made five more edits and has to work out which one
        the message is about."""
        edit = _edit_body()
        edit["phases"][0]["enforceDeliverables"] = False
        body = client.post("/v1/design-flows/validate", json=edit).json()
        assert body["valid"] is False
        assert any("gates-enforce-what-they-require" in v for v in body["violations"])

    def test_validating_does_not_save(self, client: TestClient) -> None:
        from orchestrator.design_flow.versions import get_version_store

        before = len(get_version_store().list())
        client.post("/v1/design-flows/validate", json=_edit_body(drop="firmware"))
        assert len(get_version_store().list()) == before

    def test_an_unknown_template_is_a_404(self, client: TestClient) -> None:
        assert (
            client.post(
                "/v1/design-flows/validate", json={"baseTemplateId": "nope", "phases": []}
            ).status_code
            == 404
        )


class TestSavingAnEdit:
    def test_a_save_creates_a_version_held_for_approval(self, client: TestClient) -> None:
        response = client.post("/v1/design-flows/versions", json=_edit_body(drop="firmware"))
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["versionId"].startswith("flowv_")
        assert body["status"] == "proposed"
        assert body["origin"] == "edited"
        assert any("removed phase 'firmware'" in c for c in body["changes"])
        assert "firmware" not in {p["id"] for p in body["flow"]["phases"]}

        pending = client.get("/v1/chat/tool_approvals").json()["runs"]
        assert any(r["id"] == body["approvalId"] for r in pending)

    def test_an_edit_that_breaks_an_invariant_cannot_be_saved(self, client: TestClient) -> None:
        """The acceptance criterion. 422 rather than 400: the request was
        well-formed, the flow was not."""
        edit = _edit_body()
        edit["phases"][0]["enforceDeliverables"] = False
        response = client.post("/v1/design-flows/versions", json=edit)
        assert response.status_code == 422
        assert "gates-enforce-what-they-require" in response.json()["detail"]

    def test_nothing_is_stored_when_the_save_is_refused(self, client: TestClient) -> None:
        from orchestrator.design_flow.versions import get_version_store

        before = len(get_version_store().list())
        edit = _edit_body()
        edit["phases"][0]["enforceDeliverables"] = False
        client.post("/v1/design-flows/versions", json=edit)
        assert len(get_version_store().list()) == before

    def test_saving_an_unchanged_flow_is_refused(self, client: TestClient) -> None:
        """An approval with nothing to approve teaches reviewers to click
        through, which is how a real one later gets clicked through too."""
        response = client.post("/v1/design-flows/versions", json=_edit_body())
        assert response.status_code == 400
        assert "nothing to approve" in response.json()["detail"]

    def test_a_saved_version_can_be_fetched(self, client: TestClient) -> None:
        saved = client.post("/v1/design-flows/versions", json=_edit_body(drop="electronics")).json()
        fetched = client.get(f"/v1/design-flows/versions/{saved['versionId']}").json()
        assert fetched["versionId"] == saved["versionId"]
        assert fetched["changes"] == saved["changes"]

    def test_an_unknown_version_is_a_404(self, client: TestClient) -> None:
        assert client.get("/v1/design-flows/versions/flowv_nope").status_code == 404


# ── the live run view's backend (FORGE-396) ──────────────────────────────


class TestFlowStateReportsUnknownNotIdle:
    def test_an_unqueryable_engine_says_so_rather_than_showing_an_empty_flow(
        self, client: TestClient
    ) -> None:
        """The distinction the live view turns on.

        A worker that is down and a flow that has not started render
        identically if "could not read" becomes "pending". One of those is an
        outage, and nothing on the page would say which.
        """
        created = client.post(
            "/v1/runs",
            json={
                "request": {"kind": "design_flow", "flow": "mech_v1", "goal": "g"},
                "start": False,
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]

        body = client.get(f"/v1/runs/{run_id}/flow-state").json()
        assert body["live"] is False
        assert body["detail"], "a degraded state with no reason is not actionable"
        # The phases are still listed -- a run whose shape is known and whose
        # progress is not should show the shape.
        assert len(body["phases"]) == 6
        assert {p["status"] for p in body["phases"]} == {"unknown"}

    def test_a_plain_run_is_not_pretended_to_have_phases(self, client: TestClient) -> None:
        created = client.post("/v1/runs", json={"request": {"goal": "x"}, "start": False})
        run_id = created.json()["id"]
        body = client.get(f"/v1/runs/{run_id}/flow-state").json()
        assert body["live"] is False
        assert body["phases"] == []
        assert "not a design flow" in body["detail"]

    def test_an_unknown_run_is_a_404(self, client: TestClient) -> None:
        assert client.get("/v1/runs/nope/flow-state").status_code == 404

    def test_the_phases_come_from_the_run_s_own_flow(self, client: TestClient) -> None:
        from orchestrator.design_flow.spec import get_flow

        created = client.post(
            "/v1/runs",
            json={
                "request": {"kind": "design_flow", "flow": "hardware_v1", "goal": "g"},
                "start": False,
            },
        )
        body = client.get(f"/v1/runs/{created.json()['id']}/flow-state").json()
        assert [p["id"] for p in body["phases"]] == [p.id for p in get_flow("hardware_v1").phases]
