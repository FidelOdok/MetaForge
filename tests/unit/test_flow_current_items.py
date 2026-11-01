"""What a project already has current, for generation to reuse (FORGE-571)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.design_flows.lifecycle_service import current_item_types


async def test_no_project_or_no_twin_is_not_checked() -> None:
    assert await current_item_types(object(), None) is None
    assert await current_item_types(None, "p1") is None


async def test_reusable_items_with_a_head_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import twin_core.items.service as service

    items = [
        SimpleNamespace(item_type="intent"),
        SimpleNamespace(item_type="constraint_set"),
        SimpleNamespace(item_type="cad_model"),  # not reusable: the design is the work
    ]

    async def fake_list(twin: Any, project_id: Any, **_: Any) -> list[Any]:
        return items

    monkeypatch.setattr(service, "supports_items", lambda twin: True)
    monkeypatch.setattr(service, "list_items", fake_list)
    assert await current_item_types(object(), "p1") == ("constraint_set", "intent")


async def test_a_read_error_is_not_checked_rather_than_nothing_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twin_core.items.service as service

    async def boom(*_: Any, **__: Any) -> list[Any]:
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(service, "supports_items", lambda twin: True)
    monkeypatch.setattr(service, "list_items", boom)
    assert await current_item_types(object(), "p1") is None
