"""FORGE-511 review: the assembly check is scoped to the phase window."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import TwinConstraintChecker

OLD = "2026-09-01T00:00:00+00:00"
NEW = "2026-10-01T00:00:00+00:00"
SINCE = datetime.fromisoformat("2026-09-20T00:00:00+00:00").timestamp()


def _box(x0: float, x1: float) -> dict:
    return {"min": [x0, 0, 0], "max": [x1, 10, 10]}


def _wp(wp_id: object, name: str, ts: str) -> object:
    return SimpleNamespace(
        id=wp_id,
        type=SimpleNamespace(value="cad_model"),
        name=name,
        updated_at=datetime.fromisoformat(ts),
    )


class _Twin:
    def __init__(self, meta: dict) -> None:
        self._meta = meta

    async def evaluate_constraints(self, branch: str = "main") -> object:
        return SimpleNamespace(violations=[], warnings=[], evaluated_count=0)

    async def list_constraints(self, project_id: object) -> list:
        return []

    async def get_work_product(self, node_id: object) -> object:
        return SimpleNamespace(metadata=self._meta.get(str(node_id), {}))


async def _check(wps: list, meta: dict) -> object:
    async def get_project(_pid: str) -> object:
        return SimpleNamespace(work_products=wps)

    checker = TwinConstraintChecker(_Twin(meta), SimpleNamespace(get_project=get_project))
    return await checker.check(str(uuid4()), since_ts=SINCE)


@pytest.mark.asyncio
async def test_stale_parts_from_an_earlier_window_do_not_fail_a_correct_new_assembly() -> None:
    stale1, stale2, p1, p2, asm = (uuid4() for _ in range(5))
    wps = [
        _wp(stale1, "wall_shelf_board", OLD),
        _wp(stale2, "fallback board", OLD),
        _wp(p1, "Board", NEW),
        _wp(p2, "Bracket", NEW),
        _wp(asm, "Shelf Assembly", NEW),
    ]
    meta = {
        str(asm): {
            "bbox_mm": _box(0, 20),
            "parts": [
                {"node_id": str(p1), "name": "Board", "position_bbox_mm": _box(0, 10)},
                {"node_id": str(p2), "name": "Bracket", "position_bbox_mm": _box(10, 20)},
            ],
        }
    }
    report = await _check(wps, meta)
    assert report.passed, report.violations


@pytest.mark.asyncio
async def test_two_new_parts_without_an_assembly_in_the_window_fail() -> None:
    old_asm, p1, p2 = uuid4(), uuid4(), uuid4()
    wps = [_wp(old_asm, "Old Assembly", OLD), _wp(p1, "Board", NEW), _wp(p2, "Bracket", NEW)]
    meta = {str(old_asm): {"parts": [{"node_id": str(p1), "name": "Board"}]}}
    report = await _check(wps, meta)
    assert not report.passed
    assert any("no assembly" in v for v in report.violations)


@pytest.mark.asyncio
async def test_superseded_part_is_excluded() -> None:
    old_part, new_part, other = uuid4(), uuid4(), uuid4()
    wps = [_wp(old_part, "Board v1", NEW), _wp(new_part, "Board", NEW)]
    meta = {str(new_part): {"supersedes": str(old_part)}}
    report = await _check(wps, meta)
    assert report.passed, report.violations  # only one current part: no assembly needed
    assert other
