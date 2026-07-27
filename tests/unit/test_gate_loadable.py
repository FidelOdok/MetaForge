"""The gate requires a *loadable* cad_model, not a bare node (MET-10)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import ProjectGateEvaluator, _is_loadable


class _WP:
    def __init__(self, wp_id, wp_type, *, metadata=None, content_hash=None, updated_at=None):
        self.id = wp_id
        self.type = wp_type
        self.metadata = metadata or {}
        self.content_hash = content_hash
        self.updated_at = updated_at


class _Project:
    def __init__(self, wps):
        self.work_products = wps


class _Backend:
    def __init__(self, project):
        self._project = project

    async def get_project(self, project_id):
        return self._project


class _Twin:
    """Resolves work products by id (the blob-bearing view)."""

    def __init__(self, by_id):
        self._by_id = by_id

    async def get_work_product(self, uid):
        return self._by_id.get(str(uid))


@pytest.mark.asyncio
async def test_is_loadable_variants() -> None:
    assert await _is_loadable(None, str(uuid4())) is True  # fail-open without a twin
    a, b, c, d = (str(uuid4()) for _ in range(4))
    twin = _Twin(
        {
            a: _WP(a, "cad_model", metadata={"minio_object_key": "k"}),
            b: _WP(b, "cad_model", content_hash="abc"),
            c: _WP(c, "cad_model", metadata={"file_path": "/w/x.step"}),
            d: _WP(d, "cad_model"),  # no blob anywhere
        }
    )
    assert await _is_loadable(twin, a) is True
    assert await _is_loadable(twin, b) is True
    assert await _is_loadable(twin, c) is True
    assert await _is_loadable(twin, d) is False
    assert await _is_loadable(twin, str(uuid4())) is False  # unknown id


@pytest.mark.asyncio
async def test_gate_excludes_non_loadable_cad_model() -> None:
    good, bad = str(uuid4()), str(uuid4())
    project = _Project([_WP(good, "cad_model"), _WP(bad, "cad_model")])
    twin = _Twin(
        {
            good: _WP(good, "cad_model", metadata={"minio_object_key": "k"}),
            bad: _WP(bad, "cad_model"),  # node exists but no blob
        }
    )
    ev = ProjectGateEvaluator(_Backend(project), twin=twin)
    present = await ev.present_types("p1", since_ts=0.0)
    assert "cad_model" in present  # at least one loadable cad_model

    # If the ONLY cad_model is non-loadable, the type must not count.
    project2 = _Project([_WP(bad, "cad_model")])
    ev2 = ProjectGateEvaluator(_Backend(project2), twin=twin)
    assert "cad_model" not in await ev2.present_types("p1", since_ts=0.0)


@pytest.mark.asyncio
async def test_no_twin_keeps_presence_behaviour() -> None:
    wp_id = str(uuid4())
    ev = ProjectGateEvaluator(_Backend(_Project([_WP(wp_id, "cad_model")])))
    assert "cad_model" in await ev.present_types("p1", since_ts=0.0)
