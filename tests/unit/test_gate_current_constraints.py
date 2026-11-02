"""FORGE-740: a gate evaluates only current constraint-set revisions.

The models half of a geometry check was scoped to current revisions by
FORGE-515/597; the constraints half was not, so a corrected limit could be
tightened within a run but never relaxed: the old revision kept voting. The
numbers are the chair run's: the assembly's middle extent is 500 mm, revision 1
said ``<= 420``, an abandoned revision 2 said ``<= 420`` and revision 3,
superseding revision 1, says ``<= 500``.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import TwinConstraintChecker
from twin_core.models.enums import EdgeType

TS = "2026-11-02T00:00:00+00:00"
REV1, REV2, REV3 = str(uuid4()), str(uuid4()), str(uuid4())
CAD, SIM = uuid4(), uuid4()
CHAIR = {"bbox_mm": {"min": [0, 0, 0], "max": [850, 500, 472.19]}}
LIVE_FEA = {
    "max_displacement_mm": 12.07,
    "factored_load_n": 490,
    "service_load_n": 245,
    "verdict": "pass",
}


def _c(metric: str, limit: float, set_wp: str | None) -> SimpleNamespace:
    meta = {"constraint_set_wp": set_wp} if set_wp else {}
    return SimpleNamespace(
        name=metric, metric=metric, limit=limit, operator="<=", unit="mm", metadata=meta
    )


def _wp(wp_id: object, wp_type: str, name: str) -> object:
    return SimpleNamespace(
        id=wp_id,
        type=SimpleNamespace(value=wp_type),
        name=name,
        updated_at=datetime.fromisoformat(TS),
    )


class _Graph:
    def __init__(self, statuses: dict[str, str], supersedes: dict[str, str], broken: set[str]):
        self._statuses = statuses
        self._incoming = {target: source for source, target in supersedes.items()}
        self._broken = broken

    async def get_edges(self, node_id: object, **kw: object) -> list:
        if str(node_id) in self._broken:
            raise RuntimeError("graph unavailable")
        if kw.get("edge_type") == EdgeType.REVISION_OF:
            status = self._statuses.get(str(node_id))
            return [] if status is None else [SimpleNamespace(metadata={"status": status})]
        if kw.get("edge_type") == EdgeType.SUPERSEDES and kw.get("direction") == "incoming":
            source = self._incoming.get(str(node_id))
            return [] if source is None else [SimpleNamespace(source_id=source)]
        return []


class _Twin:
    def __init__(
        self,
        constraints: list,
        statuses: dict[str, str] | None = None,
        supersedes: dict[str, str] | None = None,
        broken: set[str] | None = None,
    ) -> None:
        self._constraints = constraints
        self.graph = _Graph(statuses or {}, supersedes or {}, broken or set())
        self._nodes = {
            str(CAD): SimpleNamespace(metadata=CHAIR),
            str(SIM): SimpleNamespace(metadata={**LIVE_FEA, "source_cad_model_id": str(CAD)}),
        }

    async def list_constraints(self, project_id: object = None) -> list:
        return self._constraints

    async def get_work_product(self, node_id: object) -> object:
        return self._nodes.get(str(node_id), SimpleNamespace(metadata={}))

    async def get_edges(self, node_id: object, direction: str, edge_type: EdgeType) -> list:
        return []


def _checker(twin: _Twin) -> TwinConstraintChecker:
    async def get_project(_pid: str) -> object:
        return SimpleNamespace(
            work_products=[
                _wp(CAD, "cad_model", "Chair Assembly"),
                _wp(SIM, "simulation_result", "fea"),
            ]
        )

    return TwinConstraintChecker(twin, SimpleNamespace(get_project=get_project))


# rev 3 is the open draft of this run and supersedes rev 1; rev 2 was abandoned.
CHAIR_HISTORY = {
    "statuses": {REV1: "approved", REV2: "abandoned", REV3: "open"},
    "supersedes": {REV3: REV1},
}
WIDTHS = [
    _c("product_envelope_width", 420, REV1),
    _c("product_envelope_width", 420, REV2),
    _c("product_envelope_width", 500, REV3),
]


@pytest.mark.asyncio
async def test_a_corrected_revision_clears_its_predecessors_violation() -> None:
    out = await _checker(_Twin(WIDTHS, **CHAIR_HISTORY))._geometry_check(str(uuid4()))
    assert out.violations == []
    assert out.evaluated >= 1


@pytest.mark.asyncio
async def test_an_abandoned_revision_gets_no_vote() -> None:
    # No rev 3 here: rev 1 is the head and passes, the abandoned rev 2 must not fail it.
    constraints = [_c("product_envelope_width", 500, REV1), _c("product_envelope_width", 420, REV2)]
    twin = _Twin(constraints, statuses={REV1: "approved", REV2: "abandoned"})
    out = await _checker(twin)._geometry_check(str(uuid4()))
    assert out.violations == []


@pytest.mark.asyncio
async def test_the_current_head_still_bites() -> None:
    constraints = [*WIDTHS[:2], _c("product_envelope_width", 480, REV3)]
    out = await _checker(_Twin(constraints, **CHAIR_HISTORY))._geometry_check(str(uuid4()))
    assert len(out.violations) == 1
    assert "480" in out.violations[0]


@pytest.mark.asyncio
async def test_a_closed_draft_does_not_supersede_the_head() -> None:
    # The abandoned rev 2 once pointed a SUPERSEDES edge at rev 1; rev 1 is
    # still the head, so its real violation stands.
    constraints = [_c("product_envelope_width", 420, REV1), _c("product_envelope_width", 600, REV2)]
    twin = _Twin(
        constraints, statuses={REV1: "approved", REV2: "abandoned"}, supersedes={REV2: REV1}
    )
    out = await _checker(twin)._geometry_check(str(uuid4()))
    assert len(out.violations) == 1 and "420" in out.violations[0]


@pytest.mark.asyncio
async def test_an_unreadable_revision_status_keeps_the_constraint() -> None:
    constraints = [_c("product_envelope_width", 420, REV1)]
    twin = _Twin(constraints, broken={REV1})
    out = await _checker(twin)._geometry_check(str(uuid4()))
    assert len(out.violations) == 1


@pytest.mark.asyncio
async def test_a_constraint_with_no_set_is_kept() -> None:
    out = await _checker(_Twin([_c("product_envelope_width", 420, None)]))._geometry_check(
        str(uuid4())
    )
    assert len(out.violations) == 1


@pytest.mark.asyncio
async def test_the_analysis_check_reads_only_current_revisions() -> None:
    # 12.07 mm at 490 N is about 6.04 mm at the 245 N service load.
    constraints = [_c("service_deflection", 5, REV1), _c("service_deflection", 7, REV3)]
    twin = _Twin(constraints, **CHAIR_HISTORY)
    out = await _checker(twin)._analysis_check(str(uuid4()))
    assert out.violations == []
    assert len(out.satisfied) == 1

    stale_only = _Twin([_c("service_deflection", 5, REV1)], statuses={REV1: "approved"})
    out = await _checker(stale_only)._analysis_check(str(uuid4()))
    assert len(out.violations) == 1
