"""Tests for the MET-630 dashboard-facing parameter/script endpoints:

- ``TwinNodeResponse.geometryParameters``/``hasScript`` (surfaced from
  metadata that ``_wp_to_response`` previously silently dropped)
- ``GET /v1/twin/nodes/{node_id}/script``
- ``POST /v1/assistant/proposals`` (human-facing proposal creation)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


def _make_wp(**kwargs) -> WorkProduct:
    defaults = dict(
        name="Bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="abc123",
        format="step",
        created_by="test",
        metadata={},
    )
    defaults.update(kwargs)
    return WorkProduct(**defaults)


class TestTwinRoutesGeometryFields:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.twin.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.twin.routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def test_geometry_parameters_surfaced_on_get_node(self, client, twin) -> None:
        wp = _make_wp(
            metadata={
                "geometry_features": {
                    "parameters": {"pad_length_mm": 15},
                    "properties": {"volume_mm3": 1234.5},
                },
                "script_node_id": str(uuid4()),
            }
        )
        await twin.create_work_product(wp)

        resp = await client.get(f"/v1/twin/nodes/{wp.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["geometryParameters"]["parameters"]["pad_length_mm"] == 15
        assert body["geometryParameters"]["properties"]["volume_mm3"] == 1234.5
        assert body["hasScript"] is True

    async def test_geometry_parameters_absent_by_default(self, client, twin) -> None:
        wp = _make_wp(metadata={})
        await twin.create_work_product(wp)

        resp = await client.get(f"/v1/twin/nodes/{wp.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["geometryParameters"] is None
        assert body["hasScript"] is False


class TestGetNodeScript:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.twin.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.twin.routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    @pytest.fixture(autouse=True)
    def _clear_registry(self):
        from api_gateway.twin.git_repo_registry import init_git_registry

        yield
        init_git_registry(None)

    async def test_404_for_unknown_node(self, client, twin) -> None:
        resp = await client.get(f"/v1/twin/nodes/{uuid4()}/script")
        assert resp.status_code == 404

    async def test_404_when_node_has_no_script(self, client, twin) -> None:
        wp = _make_wp(metadata={})
        await twin.create_work_product(wp)
        resp = await client.get(f"/v1/twin/nodes/{wp.id}/script")
        assert resp.status_code == 404
        assert "no versioned script" in resp.json()["detail"]

    async def test_503_when_git_registry_not_configured(self, client, twin) -> None:
        wp = _make_wp(
            metadata={
                "script_node_id": str(uuid4()),
                "git_commit_sha": "deadbeef",
                "git_path": "mechanical/cad_src/bracket.py",
            }
        )
        await twin.create_work_product(wp)
        resp = await client.get(f"/v1/twin/nodes/{wp.id}/script")
        assert resp.status_code == 503

    async def test_success_reads_script_from_git(self, client, twin, tmp_path) -> None:
        from api_gateway.twin.git_repo_registry import GitRepoRegistry, init_git_registry

        project_id = str(uuid4())
        registry = GitRepoRegistry(twin.graph, tmp_path / "repo")
        init_git_registry(registry)
        engine = registry.for_project(project_id)
        script_id = uuid4()

        # Register a dummy graph node so commit()'s content_hash lookup succeeds.
        script_node = _make_wp(name="Bracket (script)", id=script_id, content_hash="h1")
        await twin.create_work_product(script_node)

        await engine.create_branch("main")
        version = await engine.commit(
            "main",
            "author Bracket",
            [script_id],
            "test",
            content={script_id: b"pad(15)\n"},
            paths={script_id: "mechanical/cad_src/bracket.py"},
        )

        wp = _make_wp(
            project_id=project_id,
            metadata={
                "script_node_id": str(script_id),
                "git_commit_sha": version.git_commit_sha,
                "git_path": "mechanical/cad_src/bracket.py",
            },
        )
        await twin.create_work_product(wp)

        resp = await client.get(f"/v1/twin/nodes/{wp.id}/script")
        assert resp.status_code == 200
        body = resp.json()
        assert body["script_source"] == "pad(15)\n"
        assert body["git_commit_sha"] == version.git_commit_sha
        assert body["script_node_id"] == str(script_id)


class TestCreateProposalRoute:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.assistant.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def test_create_proposal_from_human(self, client) -> None:
        resp = await client.post(
            "/v1/assistant/proposals",
            json={
                "description": "Widen the bracket pad",
                "diff": {
                    "action": "regenerate_geometry",
                    "script_source": "pad(20)\n",
                    "name": "Bracket",
                    "parameters": {"pad_length_mm": 20},
                },
                "project_id": "proj-1",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["agent_code"] == "human"
        assert body["status"] == "pending"
        assert body["diff"]["script_source"] == "pad(20)\n"

        # It's now retrievable through the existing list/decide pipeline.
        listed = await client.get("/v1/assistant/proposals")
        assert listed.status_code == 200
        assert any(p["change_id"] == body["change_id"] for p in listed.json()["proposals"])
