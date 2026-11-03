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
    twin = _Twin(ANSWERED)
    assert await current_item_types(twin, PROJECT) == ("constraint_set", "intent")


# FORGE-768: intent also settles the deciding questions, so an intent with
# no deciding answers on record is not reused.
PROJECT = "11111111-1111-1111-1111-111111111111"
ANSWERED = {"questions": [{"id": "users", "question": "Who uses it?", "answer": "adults"}]}


class _Twin:
    def __init__(self, deciding: Any = None, *, broken: bool = False) -> None:
        self.deciding = deciding
        self.broken = broken

    async def list_work_products(self, **_: Any) -> list[Any]:
        if self.broken:
            raise RuntimeError("twin down")
        metadata = {} if self.deciding is None else {"deciding_questions": self.deciding}
        return [
            SimpleNamespace(metadata={"rationale": "other"}),
            SimpleNamespace(metadata=metadata),
        ]


@pytest.mark.parametrize(
    ("twin", "reused"),
    [
        (_Twin(ANSWERED), True),
        (_Twin(None), False),
        (_Twin({"questions": []}), False),  # a malformed record is not an answer
        (_Twin(broken=True), False),  # unreadable reads as "not settled"
    ],
)
async def test_intent_is_reused_only_with_its_deciding_answers(
    monkeypatch: pytest.MonkeyPatch, twin: _Twin, reused: bool
) -> None:
    import twin_core.items.service as service

    async def fake_list(*_: Any, **__: Any) -> list[Any]:
        return [SimpleNamespace(item_type="intent"), SimpleNamespace(item_type="stakeholder_need")]

    monkeypatch.setattr(service, "supports_items", lambda twin: True)
    monkeypatch.setattr(service, "list_items", fake_list)
    current = await current_item_types(twin, PROJECT)
    assert current is not None
    assert ("intent" in current) is reused
    assert "stakeholder_need" in current


async def test_a_read_error_is_not_checked_rather_than_nothing_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twin_core.items.service as service

    async def boom(*_: Any, **__: Any) -> list[Any]:
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(service, "supports_items", lambda twin: True)
    monkeypatch.setattr(service, "list_items", boom)
    assert await current_item_types(object(), "p1") is None
