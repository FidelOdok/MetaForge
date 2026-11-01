"""One home for requirements (FORGE-528).

The constraint set item is the only source of requirement values. The prd is
rendered from it (plus the prd prose and the current intent/needs), a prd
write stores only prose and warns about values the constraint set lacks, and
the requirements decision links the constraint set revision instead of
restating it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.requirement_intelligence.matrix import build_requirement_matrix
from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.document_recorder import make_document_recorder
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from api_gateway.twin.requirements_home import (
    current_requirements,
    extract_values,
    render_prd,
    stray_values,
)
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType

PROJECT = "52852852-8528-4528-8528-528528528528"


@pytest.fixture(autouse=True)
def _blob_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


def _mass(limit: float) -> list[dict[str, Any]]:
    return [
        {
            "name": "mass",
            "metric": "mass_g",
            "operator": "<=",
            "limit": limit,
            "unit": "g",
            "verification_method": "measured on a scale",
            "acceptance_criteria": f"mass <= {limit:g} g",
        }
    ]


async def _record_set(twin: Any, limit: float) -> dict[str, Any]:
    record = make_constraint_recorder(twin, None)
    return await record(title="Widget constraints", constraints=_mass(limit), project_id=PROJECT)


async def _record_prd(twin: Any, content: str, name: str = "Widget PRD") -> dict[str, Any]:
    record = make_document_recorder(twin, None)
    return await record(
        content=content,
        name=name,
        wp_type="prd",
        domain="requirements",
        fmt="md",
        link_type="prd",
        source_tool="twin.record_document",
        project_id=PROJECT,
    )


class TestValueExtraction:
    def test_numbers_with_units_and_money(self) -> None:
        values = extract_values("Mass <= 15 g, power under 0.5 W, cost $20, 3 sensors.")
        assert [(v.number, v.unit) for v in values] == [(15.0, "g"), (0.5, "w"), (20.0, "$")]

    def test_unit_spellings_normalise(self) -> None:
        values = extract_values("weighs 12 grams and runs at 85 °C")
        assert [(v.number, v.unit) for v in values] == [(12.0, "g"), (85.0, "c")]

    def test_stray_against_constraints(self, twin) -> None:
        from twin_core.models.constraint import Constraint
        from twin_core.models.enums import ConstraintSeverity

        c = Constraint(
            name="mass",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="systems",
            source="t",
            metric="mass_g",
            limit=15,
            unit="g",
        )
        stray, matched = stray_values("mass <= 15 g and power <= 2 W", [c])
        assert stray == ["2 W"]
        assert matched == 1


class TestDerivedPrd:
    async def test_renders_current_requirements_with_refs(self, twin) -> None:
        entity = make_engineering_entity_recorder(twin, None)
        await entity(
            entity_type="intent",
            title="Widget intent",
            statement="Hold a phone on a desk",
            project_id=PROJECT,
        )
        cs = await _record_set(twin, 15)
        prd = await _record_prd(twin, "# Widget\n\nA desk stand. Out of scope: charging.")

        out = await render_prd(twin, UUID(PROJECT))

        md = out["markdown"]
        assert out["requirement_refs"] == [cs["item_ref"]]
        assert out["prose_ref"] == prd["item_ref"]
        assert "A desk stand. Out of scope: charging." in md
        assert "Hold a phone on a desk" in md and "`INT-WIDGET-INTENT@1`" in md
        row = next(line for line in md.splitlines() if "| mass |" in line)
        assert row.startswith(f"| {cs['item_ref']} | mass | mass_g | <= | 15 | g |")
        assert "measured on a scale" in row and "mass <= 15 g" in row
        assert cs["item_ref"] in out["refs"] and prd["item_ref"] in out["refs"]

    async def test_changes_when_the_constraint_set_gets_a_new_revision(self, twin) -> None:
        v1 = await _record_set(twin, 15)
        await _record_prd(twin, "A desk stand.")
        before = (await render_prd(twin, UUID(PROJECT)))["markdown"]

        v2 = await _record_set(twin, 12)
        after = await render_prd(twin, UUID(PROJECT))

        assert (v1["item_ref"], v2["item_ref"]) == (
            "CS-WIDGET-CONSTRAINTS@1",
            "CS-WIDGET-CONSTRAINTS@2",
        )
        assert "| 15 | g |" in before
        assert after["requirement_refs"] == ["CS-WIDGET-CONSTRAINTS@2"]
        rows = [line for line in after["markdown"].splitlines() if "| mass |" in line]
        assert len(rows) == 1, "the old revision's requirement must not show twice"
        assert "| 12 | g |" in rows[0] and "CS-WIDGET-CONSTRAINTS@2" in rows[0]
        assert after["requirement_count"] == 1

    async def test_no_requirements_says_where_to_record_them(self, twin) -> None:
        out = await render_prd(twin, UUID(PROJECT))
        assert out["requirement_count"] == 0
        assert "twin.record_constraint_set" in out["markdown"]


class TestPrdWrite:
    async def test_prose_is_a_prd_item_revision_and_says_where_requirements_live(
        self, twin
    ) -> None:
        await _record_set(twin, 15)
        first = await _record_prd(twin, "A desk stand, mass <= 15 g.")
        # Same project, different title: still the project's one prd.
        second = await _record_prd(twin, "A desk stand for phones.", name="Widget spec")

        assert first["item_ref"].startswith("PRD-") and first["revision"] == 1
        assert second["item_key"] == first["item_key"] and second["revision"] == 2
        assert "twin.record_constraint_set" in first["requirements_home"]
        assert first["requirement_refs"] == ["CS-WIDGET-CONSTRAINTS@1"]
        assert "requirement_warning" not in first, "15 g is in the constraint set"

    async def test_stray_values_warn_and_are_kept(self, twin) -> None:
        await _record_set(twin, 15)
        text = "A desk stand. Mass <= 15 g. Power <= 2 W. Cost under $20."
        result = await _record_prd(twin, text)

        assert result["stray_requirement_values"] == ["2 W", "$20"]
        assert "2 W" in result["requirement_warning"]
        assert "twin.record_constraint_set" in result["requirement_warning"]
        node = await twin.get_work_product(UUID(result["node_id"]))
        assert node is not None
        assert node.metadata["prose"] == text, "nothing is dropped from the prose"
        assert node.metadata["stray_requirement_values"] == ["2 W", "$20"]

    async def test_mcp_tool_returns_the_warning(self, twin) -> None:
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        result = await server.record_document(
            {
                "name": "Widget PRD",
                "content": "Battery life >= 8 h.",
                "document_type": "prd",
                "project_id": PROJECT,
            }
        )
        assert result["stray_requirement_values"] == ["8 h"]
        assert "no constraint set yet" in result["requirement_warning"]
        assert result["item_ref"].startswith("PRD-")

    async def test_other_document_types_are_unchanged(self, twin) -> None:
        record = make_document_recorder(twin, None)
        result = await record(
            content="Mass 99 g.",
            name="Notes",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
            project_id=PROJECT,
        )
        assert "requirement_warning" not in result and "item_key" not in result


class TestDecisionLinks:
    async def test_depends_on_links_the_revision_not_the_values(self, twin) -> None:
        cs = await _record_set(twin, 15)
        record = make_decision_recorder(twin, None)
        result = await record(
            title="Widget requirements basis",
            rationale="Requirements recorded in the constraint set; chosen as the basis.",
            project_id=PROJECT,
            depends_on=["CS-WIDGET-CONSTRAINTS"],
        )

        assert result["depends_on"] == [cs["item_ref"]], "a bare key is pinned to its revision"
        wp = await twin.get_work_product(UUID(result["node_id"]))
        assert wp is not None
        assert wp.metadata["depends_on"] == [cs["item_ref"]]
        assert "15" not in wp.metadata["rationale"]
        edges = await twin.graph.get_edges(
            UUID(result["node_id"]), direction="outgoing", edge_type=EdgeType.DEPENDS_ON
        )
        assert [str(e.target_id) for e in edges] == [cs["node_id"]]
        assert edges[0].metadata["item_ref"] == cs["item_ref"]

    async def test_unknown_ref_fails_before_writing(self, twin) -> None:
        record = make_decision_recorder(twin, None)
        before = len(await twin.list_work_products())
        with pytest.raises(ValueError, match="CS-NOPE"):
            await record(title="t", rationale="r", project_id=PROJECT, depends_on=["CS-NOPE@1"])
        assert len(await twin.list_work_products()) == before

    async def test_requirements_phase_links_not_restates(self) -> None:
        from api_gateway.runs.req_handlers import GoalDrivenRequirementsHandler, _normalize_req_spec
        from orchestrator.design_flow.executor import FlowContext
        from orchestrator.design_flow.spec import get_flow

        calls: list[tuple[str, dict]] = []
        prose: list[str] = []

        class _Bridge:
            async def invoke(self, tool: str, args: dict) -> dict:
                calls.append((tool, args))
                data = {"node_id": "cs1", "item_ref": "CS-WIDGET@1"}
                return {"status": "ok", "data": data if tool.endswith("constraint_set") else {}}

        async def doc_recorder(**kwargs: Any) -> dict:
            prose.append(kwargs["content"])
            return {"node_id": "prd1"}

        async def extract(goal, prior, *, provider, model):
            return _normalize_req_spec(
                {
                    "constraints": [
                        {"param": "mass", "limit": "<= 15", "unit": "g", "verify": "scale"}
                    ]
                },
                goal,
            )

        handler = GoalDrivenRequirementsHandler(_Bridge(), doc_recorder, extract=extract)
        phase = next(p for p in get_flow("hardware_v1").phases if p.id == "requirements")
        ctx = FlowContext(goal="a widget", project_id=PROJECT, completed=[])
        await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

        tools = [t for t, _ in calls]
        assert tools.index("twin.record_constraint_set") < tools.index("twin.record_decision")
        decision = next(a for t, a in calls if t == "twin.record_decision")
        assert decision["depends_on"] == ["CS-WIDGET@1"]
        assert "15" not in decision["rationale"] and "CS-WIDGET@1" in decision["rationale"]
        assert "15" not in prose[0] and "CS-WIDGET@1" in prose[0]


class TestRequirementsPageSource:
    async def test_matrix_reads_the_current_revision(self, twin) -> None:
        await _record_set(twin, 15)
        await _record_set(twin, 12)
        rows = await build_requirement_matrix(twin, UUID(PROJECT))
        assert [(r.requirementName, r.revisionRef) for r in rows] == [
            ("mass", "CS-WIDGET-CONSTRAINTS@2")
        ]
        current = await current_requirements(twin, UUID(PROJECT))
        assert [c.limit for c in current.all()] == [12.0]


class TestRoutes:
    @pytest.fixture
    def client(self, twin):
        from fastapi import FastAPI

        from api_gateway.requirement_intelligence import routes as req_routes
        from api_gateway.twin import routes
        from api_gateway.twin.prd_routes import router

        previous = routes.get_twin()
        previous_req = req_routes._twin
        routes.init_twin(twin)
        req_routes._twin = twin
        app = FastAPI()
        app.include_router(router)
        app.include_router(req_routes.router)
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        routes.init_twin(previous)
        req_routes._twin = previous_req

    async def test_project_and_item_prd(self, twin, client) -> None:
        await _record_set(twin, 15)
        prd = await _record_prd(twin, "A desk stand.")
        await _record_set(twin, 12)

        resp = await client.get(f"/v1/twin/projects/{PROJECT}/prd")
        assert resp.status_code == 200
        body = resp.json()
        assert body["requirement_refs"] == ["CS-WIDGET-CONSTRAINTS@2"]
        assert "| 12 | g |" in body["markdown"]

        resp = await client.get(f"/v1/twin/items/{prd['item_ref']}/prd")
        assert resp.status_code == 200
        assert resp.json()["prose_ref"] == prd["item_ref"]

        resp = await client.get("/v1/requirements/matrix", params={"project_id": PROJECT})
        assert resp.json()["revisionRefs"] == ["CS-WIDGET-CONSTRAINTS@2"]

    async def test_errors(self, twin, client) -> None:
        cs = await _record_set(twin, 15)
        assert (await client.get("/v1/twin/projects/nope/prd")).status_code == 400
        assert (await client.get("/v1/twin/items/PRD-NOPE/prd")).status_code == 404
        assert (await client.get(f"/v1/twin/items/{cs['item_key']}/prd")).status_code == 400
