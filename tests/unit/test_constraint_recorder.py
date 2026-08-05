"""twin.record_constraint_set — structured requirements into the twin (MET-582).

Covers the constraint recorder (expression validation → Constraint nodes +
constraint_set work product, bound together so violations attribute to the
project), the twin adapter handler, and the end-to-end chain into MET-583's
gate scoping: record → evaluate → TwinConstraintChecker sees a project-scoped
violation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.runs.gate_eval import TwinConstraintChecker
from api_gateway.twin.constraint_recorder import make_constraint_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PROJECT_ID = "22222222-2222-4222-8222-222222222222"


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(self, project_id: str, wp_id: str, name: str, wp_type: str) -> None:
        self.links.append((project_id, wp_id, name, wp_type))

    async def get_project(self, project_id: str) -> object | None:
        return SimpleNamespace(
            work_products=[SimpleNamespace(id=wp_id) for _, wp_id, _, _ in self.links]
        )


def _patch_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "digital_twin.storage.work_product_blobs.store_work_product_blob",
        lambda node_id, filename, content, *, content_type=None: f"wp/{node_id}/{filename}",
    )


_MASS_LIMIT = {
    "name": "mass_budget",
    "expression": "all(float(wp.metadata.get('mass_g', 0)) <= 60"
    " for wp in ctx.work_products(type='cad_model'))",
    "severity": "error",
    "message": "Total cad_model mass must stay under 60 g",
}


@pytest.mark.asyncio
async def test_recorder_creates_nodes_and_set_work_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_blob(monkeypatch)
    twin = InMemoryTwinAPI.create()
    backend = _FakeProjectBackend()
    record = make_constraint_recorder(twin, backend)

    out = await record(
        title="Bracket requirements",
        constraints=[_MASS_LIMIT, {"name": "sf", "expression": "True", "severity": "warning"}],
        project_id=PROJECT_ID,
    )
    assert out["node_id"] and len(out["constraint_ids"]) == 2
    assert out["project_linked"] is True
    # The set WP is linked to the project under the constraint_set type.
    assert backend.links and backend.links[0][3] == "constraint_set"
    # Constraint nodes are real and fetchable through the engine.
    from uuid import UUID

    c = await twin.constraints.get_constraint(UUID(out["constraint_ids"][0]))
    assert c is not None and c.name == "mass_budget"


@pytest.mark.asyncio
async def test_bad_expression_rejected_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_blob(monkeypatch)
    record = make_constraint_recorder(InMemoryTwinAPI.create(), None)
    with pytest.raises(ValueError, match="does not compile"):
        await record(
            title="t",
            constraints=[{"name": "broken", "expression": "mass <= )("}],
        )


@pytest.mark.asyncio
async def test_bad_severity_and_empty_set_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_blob(monkeypatch)
    record = make_constraint_recorder(InMemoryTwinAPI.create(), None)
    with pytest.raises(ValueError, match="severity"):
        await record(
            title="t", constraints=[{"name": "x", "expression": "True", "severity": "fatal"}]
        )
    with pytest.raises(ValueError, match="non-empty array"):
        await record(title="t", constraints=[])


@pytest.mark.asyncio
async def test_adapter_registers_tool_and_validates_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_blob(monkeypatch)
    twin = InMemoryTwinAPI.create()
    calls: dict[str, Any] = {}

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        calls.update(kwargs)
        return {"node_id": "n1", "constraint_ids": ["c1"]}

    server = TwinServer(twin=twin, constraint_recorder=recorder)
    assert "twin.record_constraint_set" in set(server.tool_ids)

    out = await server.record_constraint_set(
        {
            "title": "t",
            "constraints": [{"name": "x", "expression": "True"}],
            "project_id": PROJECT_ID,
        }
    )
    assert out["node_id"] == "n1" and calls["project_id"] == PROJECT_ID

    with pytest.raises(ValueError, match="title"):
        await server.record_constraint_set({"constraints": [{}]})
    with pytest.raises(ValueError, match="constraints"):
        await server.record_constraint_set({"title": "t"})


@pytest.mark.asyncio
async def test_adapter_without_recorder_does_not_expose_tool() -> None:
    twin = InMemoryTwinAPI.create()
    server = TwinServer(twin=twin)
    assert "twin.record_constraint_set" not in set(server.tool_ids)


@pytest.mark.asyncio
async def test_end_to_end_violation_scopes_to_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MET-582 -> MET-583 chain: a recorded failing constraint surfaces
    at the gate checker AS A PROJECT-SCOPED violation, because the Constraint
    node is bound to the project's constraint_set work product."""
    _patch_blob(monkeypatch)
    twin = InMemoryTwinAPI.create()
    backend = _FakeProjectBackend()
    record = make_constraint_recorder(twin, backend)

    await record(
        title="Impossible requirements",
        constraints=[{"name": "always_fails", "expression": "False", "severity": "error"}],
        project_id=PROJECT_ID,
    )

    checker = TwinConstraintChecker(twin, backend)
    report = await checker.check(PROJECT_ID)
    assert report.checked and not report.passed
    assert any("always_fails" in v for v in report.violations)

    # A foreign project (no overlapping work products) does NOT inherit it.
    class _OtherBackend(_FakeProjectBackend):
        async def get_project(self, project_id: str) -> object:
            return SimpleNamespace(work_products=[SimpleNamespace(id="unrelated")])

    other = await TwinConstraintChecker(twin, _OtherBackend()).check(OTHER_PROJECT_ID)
    assert other.passed
