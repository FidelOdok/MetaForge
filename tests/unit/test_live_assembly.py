"""The live assembly view: placement tool, hub, routes, sidecar publisher (FORGE-647)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.twin.live import LiveAssemblyHub, live_event_stream
from api_gateway.twin.live_routes import router as live_router
from mcp_core.guardrails import BOOKKEEPING
from mcp_core.live_events import publish_live_event, set_live_event_publisher
from metaforge.mcp.live_publisher import http_live_publisher
from tool_registry.tools.twin.adapter import TwinServer


def _placed(node: str, name: str = "Base", **over: Any) -> dict[str, Any]:
    return {
        "type": "assembly.part_placed",
        "assembly": "Pi5",
        "part": {"node_id": node, "name": name},
        "position": [1.0, 2.0, 3.0],
        "rotation": [0.0, 90.0, 0.0],
        **over,
    }


def test_hub_keeps_placements_and_snapshots_them_in_order() -> None:
    hub = LiveAssemblyHub()
    hub.publish("p1", _placed("a"))
    hub.publish("p1", _placed("b", "Lid"))
    hub.publish("p1", _placed("a", position=[9.0, 9.0, 9.0]))  # a move, not a new part
    snap = hub.snapshot("p1")
    parts = snap["assemblies"][0]["parts"]
    assert [p["node_id"] for p in parts] == ["b", "a"]  # ordered by latest change
    assert parts[1]["position"] == [9.0, 9.0, 9.0]
    assert snap["seq"] == 3
    assert hub.snapshot("other")["assemblies"] == []


def test_hub_removes_and_clears() -> None:
    hub = LiveAssemblyHub()
    hub.publish("p1", _placed("a"))
    hub.publish("p1", _placed("b"))
    hub.publish("p1", {**_placed("a"), "type": "assembly.part_removed"})
    assert [p["node_id"] for p in hub.snapshot("p1")["assemblies"][0]["parts"]] == ["b"]
    hub.publish("p1", {"type": "assembly.cleared", "assembly": "Pi5"})
    assert hub.snapshot("p1")["assemblies"][0]["parts"] == []


@pytest.mark.asyncio
async def test_stream_sends_snapshot_then_each_change_without_duplicating() -> None:
    hub = LiveAssemblyHub()
    hub.publish("p1", _placed("a"))
    frames: list[str] = []

    async def read() -> None:
        async for frame in live_event_stream("p1", hub):
            frames.append(frame)

    task = asyncio.create_task(read())
    await asyncio.sleep(0.01)
    hub.publish("p1", _placed("b", "Lid"))
    await asyncio.sleep(0.01)
    hub.close("p1")
    await asyncio.wait_for(task, timeout=2)
    names = [f.split("event: ")[1].split("\n")[0] for f in frames]
    assert names == ["live.snapshot", "live.event"]
    snapshot = json.loads(frames[0].split("data: ")[1])
    assert [p["node_id"] for p in snapshot["assemblies"][0]["parts"]] == ["a"]
    assert frames[1].startswith("id: 2\n")


def test_ingest_route_publishes_and_validates() -> None:
    from api_gateway.twin import live as live_mod

    app = FastAPI()
    app.include_router(live_router)
    client = TestClient(app)
    pid = str(uuid4())
    body = _placed("a")
    resp = client.post(f"/v1/twin/projects/{pid}/live-events", json=body)
    assert resp.status_code == 202
    state = client.get(f"/v1/twin/projects/{pid}/live/state").json()
    assert state["assemblies"][0]["parts"][0]["node_id"] == "a"
    bad = client.post(f"/v1/twin/projects/{pid}/live-events", json={**body, "position": [1, 2]})
    assert bad.status_code == 422
    unknown = client.post(f"/v1/twin/projects/{pid}/live-events", json={**body, "type": "nope"})
    assert unknown.status_code == 422
    live_mod.hub._state.pop(pid, None)


@pytest.mark.asyncio
async def test_publish_live_event_reports_whether_anything_could_see_it() -> None:
    set_live_event_publisher(None)
    assert await publish_live_event("p", {"type": "x"}) is False
    seen: list[tuple[str, dict[str, Any]]] = []

    async def ok(project_id: str, event: dict[str, Any]) -> None:
        seen.append((project_id, event))

    async def boom(project_id: str, event: dict[str, Any]) -> None:
        raise RuntimeError("gateway down")

    try:
        set_live_event_publisher(ok)
        assert await publish_live_event("p", {"type": "x"}) is True
        set_live_event_publisher(boom)
        assert await publish_live_event("p", {"type": "x"}) is False
    finally:
        set_live_event_publisher(None)
    assert seen == [("p", {"type": "x"})]


@pytest.mark.asyncio
async def test_http_publisher_posts_to_the_gateway_with_its_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gateway:8000/")
    monkeypatch.setenv("METAFORGE_GATEWAY_API_KEY", "k")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"seq": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publish = http_live_publisher(client)
        assert publish is not None
        await publish("p1", _placed("a"))
    assert captured["url"] == "http://gateway:8000/v1/twin/projects/p1/live-events"
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["part"]["node_id"] == "a"


def test_http_publisher_is_none_without_a_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)
    assert http_live_publisher() is None


# -- the tool ---------------------------------------------------------------


class _Twin:
    def __init__(self, node: Any) -> None:
        self._node = node

    async def get_work_product(self, _id: Any) -> Any:
        return self._node


def _server(node: Any) -> TwinServer:
    return TwinServer(twin=_Twin(node))


def _node(project_id: str, type_: str = "cad_model") -> Any:
    return SimpleNamespace(
        name="Pi5 base", type=SimpleNamespace(value=type_), project_id=project_id
    )


@pytest.mark.asyncio
async def test_place_part_publishes_a_placement_and_says_whether_it_was_live() -> None:
    pid = str(uuid4())
    seen: list[tuple[str, dict[str, Any]]] = []

    async def pub(project_id: str, event: dict[str, Any]) -> None:
        seen.append((project_id, event))

    set_live_event_publisher(pub)
    try:
        server = _server(_node(pid))
        part = str(uuid4())
        out = await server.place_part(
            {"node_id": part, "assembly": "Pi5", "project_id": pid, "position": [1, 2, 3]}
        )
    finally:
        set_live_event_publisher(None)
    assert out["live"] is True and out["action"] == "placed"
    project, event = seen[0]
    assert project == pid
    assert event["type"] == "assembly.part_placed"
    assert event["part"] == {"node_id": part, "name": "Pi5 base"}
    assert event["position"] == [1.0, 2.0, 3.0] and event["rotation"] == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_place_part_says_live_false_when_nothing_is_listening() -> None:
    set_live_event_publisher(None)
    pid = str(uuid4())
    out = await _server(_node(pid)).place_part(
        {"node_id": str(uuid4()), "assembly": "A", "project_id": pid}
    )
    assert out["live"] is False


@pytest.mark.asyncio
async def test_place_part_refuses_what_is_not_a_part_in_this_project() -> None:
    pid = str(uuid4())
    part = str(uuid4())
    with pytest.raises(ValueError, match="does not exist"):
        await _server(None).place_part({"node_id": part, "assembly": "A", "project_id": pid})
    with pytest.raises(ValueError, match="not a cad_model"):
        await _server(_node(pid, "document")).place_part(
            {"node_id": part, "assembly": "A", "project_id": pid}
        )
    with pytest.raises(ValueError, match="different project"):
        await _server(_node(str(uuid4()))).place_part(
            {"node_id": part, "assembly": "A", "project_id": pid}
        )
    with pytest.raises(ValueError, match="no project"):
        await _server(_node(pid)).place_part({"node_id": part, "assembly": "A"})
    with pytest.raises(ValueError, match="three numbers"):
        await _server(_node(pid)).place_part(
            {"node_id": part, "assembly": "A", "project_id": pid, "position": [1, 2]}
        )


def test_place_part_is_registered_and_not_held_for_approval() -> None:
    assert "twin.place_part" in _server(None).tool_ids
    assert "twin.place_part" in BOOKKEEPING
