"""Project scoping for GET /v1/twin/nodes (MET-491).

The Digital Twin list endpoint gained a ``project_id`` filter so the
dashboard can show one project at a time instead of every node globally.
Omitted / empty = all projects (incl. unscoped); a specific id returns
only that project's nodes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from api_gateway.twin import routes
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


def _wp(name: str, project_id: UUID | None) -> WorkProduct:
    now = datetime.now(UTC)
    return WorkProduct(
        id=uuid4(),
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="/workspace/x.step",
        content_hash="h",
        format="step",
        project_id=project_id,
        created_at=now,
        updated_at=now,
        created_by="test",
    )


async def _seed(*wps: WorkProduct) -> None:
    twin = InMemoryTwinAPI.create()
    for wp in wps:
        await twin.create_work_product(wp)
    routes._twin = twin  # route reads the module-level twin


async def test_scoped_to_project_returns_only_that_project() -> None:
    pa, pb = uuid4(), uuid4()
    await _seed(_wp("A", pa), _wp("B", pb), _wp("Unscoped", None))

    resp = await routes.list_twin_nodes(project_id=str(pa))

    assert {n.name for n in resp.nodes} == {"A"}
    assert resp.total == 1


async def test_no_project_returns_everything_including_unscoped() -> None:
    pa = uuid4()
    await _seed(_wp("A", pa), _wp("Unscoped", None))

    resp = await routes.list_twin_nodes()

    assert {n.name for n in resp.nodes} == {"A", "Unscoped"}
    assert resp.total == 2


async def test_empty_project_id_is_treated_as_all_projects() -> None:
    pa = uuid4()
    await _seed(_wp("A", pa), _wp("Unscoped", None))

    resp = await routes.list_twin_nodes(project_id="")

    assert resp.total == 2


async def test_invalid_project_id_returns_400() -> None:
    await _seed(_wp("A", uuid4()))

    with pytest.raises(HTTPException) as exc:
        await routes.list_twin_nodes(project_id="not-a-uuid")

    assert exc.value.status_code == 400
