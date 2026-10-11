"""FORGE-597: a revision a retry turned down leaves the gate window."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import TwinConstraintChecker
from twin_core.models.enums import EdgeType

T1 = "2026-10-01T00:00:00+00:00"
T2 = "2026-10-02T00:00:00+00:00"


def _box(x0: float, x1: float) -> dict:
    return {"min": [x0, 0, 0], "max": [x1, 10, 10]}


def _wp(wp_id: object, name: str, ts: str) -> object:
    return SimpleNamespace(
        id=wp_id,
        type=SimpleNamespace(value="cad_model"),
        name=name,
        updated_at=datetime.fromisoformat(ts),
    )


class _Graph:
    def __init__(self, statuses: dict[str, str]) -> None:
        self._statuses = statuses

    async def get_edges(self, node_id: object, **kw: object) -> list:
        if kw.get("edge_type") != EdgeType.REVISION_OF:
            return []
        status = self._statuses.get(str(node_id))
        return [] if status is None else [SimpleNamespace(metadata={"status": status})]


class _Twin:
    def __init__(self, meta: dict, statuses: dict[str, str]) -> None:
        self._meta = meta
        self.graph = _Graph(statuses)

    async def evaluate_constraints(self, branch: str = "main") -> object:
        return SimpleNamespace(violations=[], warnings=[], evaluated_count=0)

    async def list_constraints(self, project_id: object) -> list:
        return []

    async def get_work_product(self, node_id: object) -> object:
        return SimpleNamespace(metadata=self._meta.get(str(node_id), {}))


async def _check(wps: list, meta: dict, statuses: dict[str, str]) -> object:
    async def get_project(_pid: str) -> object:
        return SimpleNamespace(work_products=wps)

    checker = TwinConstraintChecker(_Twin(meta, statuses), SimpleNamespace(get_project=get_project))
    return await checker.check(str(uuid4()), since_ts=0.0)


def _assembly(p1: object, p2: object) -> dict:
    return {
        "bbox_mm": _box(0, 20),
        "parts": [
            {"node_id": str(p1), "name": "Top", "position_bbox_mm": _box(0, 10)},
            {"node_id": str(p2), "name": "Leg", "position_bbox_mm": _box(10, 20)},
        ],
    }


@pytest.mark.parametrize("status", ["abandoned", "rejected"])
@pytest.mark.asyncio
async def test_a_closed_draft_model_does_not_fail_the_retry(status: str) -> None:
    # Attempt 2 left a loose analysis set; the retry turned it down and
    # attempt 3 built a correct assembly. The closed draft must not count.
    p1, p2, asm, loose = uuid4(), uuid4(), uuid4(), uuid4()
    wps = [
        _wp(p1, "Top", T2),
        _wp(p2, "Leg", T2),
        _wp(asm, "Table Assembly", T2),
        _wp(loose, "table_fea_analysis_set", T1),
    ]
    report = await _check(wps, {str(asm): _assembly(p1, p2)}, {str(loose): status})
    assert report.passed, report.violations


@pytest.mark.asyncio
async def test_the_loose_model_still_fails_while_its_draft_is_open() -> None:
    p1, p2, asm, loose = uuid4(), uuid4(), uuid4(), uuid4()
    wps = [
        _wp(p1, "Top", T2),
        _wp(p2, "Leg", T2),
        _wp(asm, "Table Assembly", T2),
        _wp(loose, "table_fea_analysis_set", T1),
    ]
    report = await _check(wps, {str(asm): _assembly(p1, p2)}, {str(loose): "open"})
    assert not report.passed


@pytest.mark.asyncio
async def test_an_older_revision_stands_in_when_the_newest_was_turned_down() -> None:
    # Same name, two revisions: the newer one was abandoned, so the older
    # committed one is what the gate sees.
    p1, p2_old, p2_new, asm = uuid4(), uuid4(), uuid4(), uuid4()
    wps = [
        _wp(p1, "Top", T2),
        _wp(p2_old, "Leg", T1),
        _wp(p2_new, "Leg", T2),
        _wp(asm, "Table Assembly", T2),
    ]
    report = await _check(wps, {str(asm): _assembly(p1, p2_old)}, {str(p2_new): "abandoned"})
    assert report.passed, report.violations
