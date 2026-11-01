"""A simulation_result node has to carry its mesh stats (FORGE-305).

FORGE-246 persisted FEA results as real ``SIMULATION_RESULT`` work products:
max von Mises, max displacement, load case, mesh stats. FORGE-279 added
``GET /v1/simulation/results`` so the Sim page could list them.

Nothing surfaced them from a *node id*, which is how the Twin Viewer's
inspector reaches a work product. The scalar numbers came through on
``properties``, but ``mesh_stats`` is an object, and ``_wp_to_response``'s
projection keeps only JSON primitives -- so the one field that says how much
to trust the numbers was the one field dropped. Same silent drop MET-630 had
to fix for ``geometry_features``.
"""

from __future__ import annotations

from typing import Any

import pytest

from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

_MESH_STATS = {"num_nodes": 12500, "num_elements": 48000, "min_jacobian": 0.42}


def _result_wp(**metadata: Any) -> WorkProduct:
    base: dict[str, Any] = {
        "max_von_mises_mpa": 182.43,
        "max_displacement_mm": 0.4126,
        "load_case": "tip-load-500N",
        "mesh_stats": dict(_MESH_STATS),
    }
    base.update(metadata)
    return WorkProduct(
        name="bracket-fea-run-3",
        type=WorkProductType.SIMULATION_RESULT,
        domain="mechanical",
        file_path="",
        content_hash="feaabc",
        format="json",
        created_by="test",
        metadata=base,
    )


@pytest.fixture
def client():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api_gateway.twin.routes import router

    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def twin():
    from api_gateway.twin.routes import _twin

    _twin._graph._nodes.clear()
    _twin._graph._outgoing.clear()
    _twin._graph._incoming.clear()
    return _twin


class TestMeshStatsReachTheInspector:
    async def test_mesh_stats_are_surfaced_on_get_node(self, client, twin) -> None:
        wp = _result_wp()
        await twin.create_work_product(wp)

        body = (await client.get(f"/v1/twin/nodes/{wp.id}")).json()
        assert body["meshStats"] == _MESH_STATS

    async def test_properties_still_drops_them_which_is_why_the_field_exists(
        self, client, twin
    ) -> None:
        """Stated as an assertion so the reason for the extra field cannot be
        mistaken for redundancy. `properties` is typed scalar-only; an object
        value does not reach the client through it at all."""
        wp = _result_wp()
        await twin.create_work_product(wp)

        body = (await client.get(f"/v1/twin/nodes/{wp.id}")).json()
        assert "mesh_stats" not in body["properties"]
        # The scalars do come through, and the inspector reads them from there.
        assert body["properties"]["max_von_mises_mpa"] == 182.43
        assert body["properties"]["max_displacement_mm"] == 0.4126
        assert body["properties"]["load_case"] == "tip-load-500N"

    async def test_they_appear_in_the_list_route_too(self, client, twin) -> None:
        """The inspector is reached by clicking a row in the explorer, which is
        populated by the list route -- so a field present only on the detail
        route would be absent exactly when it is first needed."""
        wp = _result_wp()
        await twin.create_work_product(wp)

        nodes = (await client.get("/v1/twin/nodes")).json()["nodes"]
        assert [n["meshStats"] for n in nodes] == [_MESH_STATS]

    async def test_absent_when_the_result_recorded_none(self, client, twin) -> None:
        wp = _result_wp(mesh_stats=None)
        await twin.create_work_product(wp)

        body = (await client.get(f"/v1/twin/nodes/{wp.id}")).json()
        assert body["meshStats"] is None

    async def test_a_non_object_mesh_stats_is_not_passed_through(self, client, twin) -> None:
        """An agent writing a string here should not make the field a string:
        the dashboard iterates it as an object, and `Object.entries('abc')`
        would render three numbered rows of characters."""
        wp = _result_wp(mesh_stats="48000 elements")
        await twin.create_work_product(wp)

        body = (await client.get(f"/v1/twin/nodes/{wp.id}")).json()
        assert body["meshStats"] is None

    async def test_other_node_types_are_unaffected(self, client, twin) -> None:
        wp = WorkProduct(
            name="Bracket",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="abc123",
            format="step",
            created_by="test",
            metadata={},
        )
        await twin.create_work_product(wp)

        body = (await client.get(f"/v1/twin/nodes/{wp.id}")).json()
        assert body["meshStats"] is None


class TestItIsTheSameMetadataKeyTheSimPageReads:
    async def test_both_routes_report_the_same_mesh_stats(self, client, twin) -> None:
        """Two read paths over one work product. If they diverged, the Sim
        page and the inspector would disagree about the same FEA run, which is
        worse than one of them not showing it."""
        from api_gateway.simulation.routes import _wp_to_simulation_result

        wp = _result_wp()
        await twin.create_work_product(wp)

        node = (await client.get(f"/v1/twin/nodes/{wp.id}")).json()
        assert node["meshStats"] == _wp_to_simulation_result(wp).meshStats
