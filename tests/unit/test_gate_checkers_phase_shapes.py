"""FORGE-484: the gate check must enforce deliverables on the Temporal path,
where the phase reaches the activity as a plain dict, not an object."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api_gateway.runs.routes import _GateCheckers
from orchestrator.design_flow.grounding import UNGROUNDED_BANNER, is_ungrounded, phase_status


class _Evaluator:
    def __init__(self, present: set[str]) -> None:
        self._present = present

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]:
        return set(self._present)


class _Constraints:
    async def check(self, project_id: str | None, since_ts: float = 0.0) -> SimpleNamespace:
        return SimpleNamespace(passed=True, checked=True, violations=[])


def _checkers(present: set[str]) -> _GateCheckers:
    return _GateCheckers(_Evaluator(present), _Constraints(), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("as_dict", [True, False])
async def test_missing_required_deliverable_is_not_ready(as_dict: bool) -> None:
    phase: object = {"required_deliverables": ["intent"]}
    if not as_dict:
        phase = SimpleNamespace(required_deliverables=["intent"])
    check = await _checkers(set()).evaluate(phase, "p1")
    assert check.checked is True
    assert check.ready is False
    assert check.missing == ["intent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("as_dict", [True, False])
async def test_recorded_deliverable_is_ready(as_dict: bool) -> None:
    phase: object = {"required_deliverables": ["intent"]}
    if not as_dict:
        phase = SimpleNamespace(required_deliverables=["intent"])
    check = await _checkers({"intent"}).evaluate(phase, "p1")
    assert check.checked is True
    assert check.ready is True


def test_ungrounded_summary_is_never_completed() -> None:
    summary = f"{UNGROUNDED_BANNER}\n\nI built it."
    assert is_ungrounded(summary)
    assert phase_status(summary, "completed") == "ungrounded"
    assert phase_status("I built it.", "completed") == "completed"


@pytest.mark.asyncio
async def test_recorded_intent_entity_satisfies_intent_deliverable() -> None:
    """FORGE-484 live case: an engineering entity of entity_type 'intent'
    (linked into the project the way the entity recorder does) satisfies the
    'intent' deliverable, read through the real ProjectGateEvaluator."""
    from api_gateway.projects.backend import InMemoryProjectBackend
    from api_gateway.runs.gate_eval import ProjectGateEvaluator

    backend = InMemoryProjectBackend.create()
    project = await backend.create_project(name="Wall Shelf", description="d", status="active")
    pid = str(project.id)
    checkers = _GateCheckers(ProjectGateEvaluator(backend), _Constraints(), None)
    phase = {"required_deliverables": ["intent"]}

    missing = await checkers.evaluate(phase, pid)
    assert missing.ready is False and missing.missing == ["intent"]

    await backend.link_work_product(
        pid, "0d871c6f-0111-4b29-a120-aa99a3677373", "Wall Shelf Project Intent", "intent"
    )
    ready = await checkers.evaluate(phase, pid)
    assert ready.ready is True
    assert ready.missing == []
    assert "intent" in ready.present


@pytest.mark.asyncio
async def test_worker_wires_real_stores_before_gate_check(monkeypatch) -> None:
    """The worker process must not evaluate gates against the in-memory defaults."""
    from api_gateway.projects import routes as project_routes
    from api_gateway.runs import flow_worker
    from api_gateway.twin import routes as twin_routes

    sentinel_backend = object()
    sentinel_twin = object()

    async def fake_db() -> None:
        return None

    async def fake_backend() -> object:
        return sentinel_backend

    async def fake_twin(*a: object, **k: object) -> object:
        return sentinel_twin

    import api_gateway.projects.backend as backend_mod
    import api_gateway.server as server_mod
    from twin_core.api import InMemoryTwinAPI

    monkeypatch.setattr(server_mod, "_init_database", fake_db)
    monkeypatch.setattr(backend_mod, "create_project_backend", fake_backend)
    monkeypatch.setattr(InMemoryTwinAPI, "create_from_env", staticmethod(fake_twin))
    monkeypatch.setattr(flow_worker, "_stores_ready", False)
    prev_backend = project_routes.get_project_backend()
    prev_twin = twin_routes.get_twin()
    try:
        await flow_worker.ensure_gate_stores()
        assert project_routes.get_project_backend() is sentinel_backend
        assert twin_routes.get_twin() is sentinel_twin
    finally:
        project_routes.init_project_backend(prev_backend)  # type: ignore[arg-type]
        twin_routes.init_twin(prev_twin)
        project_routes.init_twin(prev_twin)
