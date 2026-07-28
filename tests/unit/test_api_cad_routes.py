"""Route-level tests for the CAD compile endpoint (MET-10).

Exercises the handler logic around the LLM translation — the CompileResponse
assembly, the geometric-feasibility warnings, and the 422 error paths — with the
harness stubbed, so no live model or build machinery is needed. The build path
(/assembly, /from-text) is covered by the builder + compiler unit tests.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_gateway.chat.harness_backend as harness
from api_gateway.cad.routes import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _stub_translation(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    """Make the harness return *reply* for any translation prompt."""

    async def fake_run_chat_turn(prompt: str, **kwargs: Any) -> str:
        return reply

    monkeypatch.setattr(harness, "run_chat_turn", fake_run_chat_turn)


_GOOD = (
    '{"name":"Bracket","parts":[{"name":"Body","kind":"box",'
    '"parameters":{"width":60,"length":40,"height":8}}]}'
)


def test_compile_returns_spec_and_buildable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_translation(monkeypatch, f"```json\n{_GOOD}\n```")
    resp = client.post("/v1/cad/compile", json={"description": "a 60x40x8 bracket"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["buildable"] is True
    assert body["errors"] == []
    assert body["spec"]["name"] == "Bracket"
    assert body["spec"]["parts"][0]["kind"] == "box"


def test_compile_flags_infeasible_geometry_without_failing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fillet 5 on an 8mm-thick plate (half = 4) is infeasible — surfaced as a
    # warning with buildable:false, NOT a hard error.
    spec = (
        '{"name":"Plate","parts":[{"name":"P","kind":"box",'
        '"parameters":{"width":60,"length":40,"height":8},"fillet":5}]}'
    )
    _stub_translation(monkeypatch, spec)
    resp = client.post("/v1/cad/compile", json={"description": "a rounded plate"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["buildable"] is False
    assert body["errors"] and "fillet" in body["errors"][0]


def test_compile_422_on_unparseable_model_output(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_translation(monkeypatch, "I'm sorry, I can't do that.")
    resp = client.post("/v1/cad/compile", json={"description": "a widget"})
    assert resp.status_code == 422
    assert "translate" in resp.json()["detail"].lower()


def test_compile_422_on_structurally_invalid_spec(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Valid JSON + right shape for extract_spec, but 'blob' is not a real kind,
    # so the CreateAssemblyRequest validation rejects it.
    bad = '{"name":"X","parts":[{"name":"P","kind":"blob","parameters":{}}]}'
    _stub_translation(monkeypatch, bad)
    resp = client.post("/v1/cad/compile", json={"description": "a blob"})
    assert resp.status_code == 422
    assert "invalid" in resp.json()["detail"].lower()


def test_compile_honours_name_override(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_translation(monkeypatch, _GOOD)
    resp = client.post("/v1/cad/compile", json={"description": "a bracket", "name": "My Bracket"})
    assert resp.status_code == 200
    assert resp.json()["spec"]["name"] == "My Bracket"
