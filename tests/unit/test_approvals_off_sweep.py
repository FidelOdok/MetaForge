"""With METAFORGE_APPROVALS=off, nothing waits for a person except orders and failing gates."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from api_gateway.approvals import approvals_off, service
from api_gateway.approvals.schemas import ApprovalItem


def _item(item_id: str, kind: str = "tool_call", **extra: Any) -> ApprovalItem:
    base: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "status": "pending",
        "title": item_id,
        "allowed_decisions": ["approve", "reject"],
        "decidable": True,
    }
    base.update(extra)
    return ApprovalItem(**base)


def test_a_pending_item_that_offers_approve_is_approved() -> None:
    assert (
        approvals_off.why_it_waits(_item("tool:1", detail={"tool": "twin.create_baseline"})) is None
    )
    assert approvals_off.why_it_waits(_item("gate:1", kind="gate")) is None
    assert approvals_off.why_it_waits(_item("upload:1", kind="upload")) is None


def test_an_order_still_waits() -> None:
    order = _item("tool:2", detail={"tool": "vendor.place_order"})
    assert "spends money" in str(approvals_off.why_it_waits(order))


def test_a_gate_whose_checks_fail_still_waits() -> None:
    failing = _item("gate:2", kind="gate", allowed_decisions=["reject", "retry"])
    assert "cannot be approved" in str(approvals_off.why_it_waits(failing))
    decided = _item("gate:3", kind="gate", status="approved", allowed_decisions=[])
    assert approvals_off.why_it_waits(decided) == "not pending"


@pytest.fixture
def items(monkeypatch: pytest.MonkeyPatch) -> list[ApprovalItem]:
    listed = [
        _item("tool:held", kind="human_authority", detail={"tool": "twin.imagine_handover"}),
        _item("tool:order", kind="human_authority", detail={"tool": "vendor.place_order"}),
        _item("gate:ready", kind="gate"),
        _item("gate:failing", kind="gate", allowed_decisions=["reject"]),
        _item("rerun:a:b", kind="rerun"),
        _item("change:moved", kind="design_change"),
    ]

    async def list_items(_app: Any) -> list[ApprovalItem]:
        return listed

    monkeypatch.setattr(service, "list_items", list_items)
    return listed


async def test_the_sweep_approves_through_each_kinds_route_as_the_switch(
    monkeypatch: pytest.MonkeyPatch, items: list[ApprovalItem]
) -> None:
    monkeypatch.setenv("METAFORGE_APPROVALS", "off")
    calls: list[dict[str, Any]] = []

    async def decide(_app: Any, item_id: str, **kw: Any) -> None:
        if item_id == "change:moved":
            raise HTTPException(status_code=409, detail="moved on")
        calls.append({"id": item_id, **kw})

    monkeypatch.setattr(service, "decide", decide)
    approved = await approvals_off.sweep_once(object())
    assert approved == ["tool:held", "gate:ready", "rerun:a:b"]
    assert all(c["decision"] == "approve" for c in calls)
    assert {c["approver"].actor_id for c in calls} == {"system:approvals-off"}
    assert "METAFORGE_APPROVALS=off" in calls[0]["reason"]


async def test_with_approvals_on_the_sweep_does_nothing(
    monkeypatch: pytest.MonkeyPatch, items: list[ApprovalItem]
) -> None:
    monkeypatch.delenv("METAFORGE_APPROVALS", raising=False)

    async def decide(*_a: Any, **_k: Any) -> None:
        raise AssertionError("nothing is decided while approvals are on")

    monkeypatch.setattr(service, "decide", decide)
    assert await approvals_off.sweep_once(object()) == []
