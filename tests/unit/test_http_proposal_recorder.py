"""Unit tests for the cross-process HTTP proposal recorder (MET-552).

The MCP sidecar uses ``make_http_proposal_recorder`` to forward
``twin.propose_change`` calls to the gateway's ``POST /v1/assistant/proposals``
so proposals filed by an external agent (e.g. LibreChat over MCP) land in the
gateway's ApprovalWorkflow and show up in the dashboard.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from api_gateway.assistant.proposal_recorder import make_http_proposal_recorder


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_forwards_and_returns_change_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def post(self, url: str, json: dict[str, Any]) -> _FakeResp:
            captured["url"] = url
            captured["json"] = json
            return _FakeResp({"change_id": "abc-123", "status": "pending"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    recorder = make_http_proposal_recorder("http://gateway:8000/")
    good = str(uuid4())
    out = await recorder(
        agent_code="librechat",
        description="fillet base plate 4mm",
        diff={"action": "record_decision"},
        work_products=["not-a-uuid", good],
    )

    assert out == {"change_id": "abc-123", "status": "pending"}
    assert captured["url"] == "http://gateway:8000/v1/assistant/proposals"
    # Invalid UUID dropped; valid one kept.
    assert captured["json"]["work_products_affected"] == [good]
    assert captured["json"]["agent_code"] == "librechat"


@pytest.mark.asyncio
async def test_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def post(self, *a: Any, **k: Any) -> _FakeResp:
            raise RuntimeError("gateway down")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    recorder = make_http_proposal_recorder("http://gateway:8000")
    out = await recorder(agent_code=None, description="d", diff={})

    # Never raises — returns an error payload so the tool call doesn't hard-fail.
    assert "error" in out
    assert "gateway down" in out["error"]
