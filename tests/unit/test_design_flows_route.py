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
