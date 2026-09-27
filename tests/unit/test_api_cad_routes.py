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
from api_gateway.cad.routes import _is_transient_failure, router


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


# ---------------------------------------------------------------------------
# FORGE-249: classifying contention failures for the 503 + Retry-After path
# ---------------------------------------------------------------------------


class FreecadWorkerCrashedError(RuntimeError):
    """Stand-in for tool_registry.tools.freecad.worker_pool's real class --
    api_gateway must not import tool_registry (see api_gateway/CLAUDE.md), so
    the route classifies this kind of failure by class name (matched below),
    not isinstance -- hence no leading underscore: the *name* is the point."""


class ServerDisconnectedError(Exception):
    """Stand-in for aiohttp.ServerDisconnectedError -- same reasoning."""


def _raised_with_cause(outer: Exception, cause: BaseException) -> Exception:
    try:
        raise outer from cause
    except Exception as exc:  # noqa: BLE001 — capturing the chained exception
        return exc


class TestIsTransientFailure:
    def test_a_worker_crash_is_transient(self) -> None:
        exc = _raised_with_cause(RuntimeError("wrapped"), FreecadWorkerCrashedError("crashed"))
        assert _is_transient_failure(exc) is True

    def test_a_dropped_connection_is_transient(self) -> None:
        exc = _raised_with_cause(RuntimeError("wrapped"), ServerDisconnectedError())
        assert _is_transient_failure(exc) is True

    def test_a_bare_connection_error_is_transient(self) -> None:
        exc = _raised_with_cause(RuntimeError("wrapped"), ConnectionResetError("reset"))
        assert _is_transient_failure(exc) is True

    def test_a_bare_timeout_is_transient(self) -> None:
        exc = _raised_with_cause(RuntimeError("wrapped"), TimeoutError("timed out"))
        assert _is_transient_failure(exc) is True

    def test_a_deterministic_validation_error_is_not_transient(self) -> None:
        exc = _raised_with_cause(RuntimeError("wrapped"), ValueError("bad geometry"))
        assert _is_transient_failure(exc) is False

    def test_an_unchained_exception_checks_itself(self) -> None:
        # No `from` clause -- __cause__ is None, so the exception itself is
        # the thing to classify.
        assert _is_transient_failure(TimeoutError()) is True
        assert _is_transient_failure(ValueError("bad spec")) is False


_ONE_PART = [
    {"name": "Body", "kind": "box", "parameters": {"width": 60, "length": 40, "height": 8}}
]


def test_assembly_returns_503_with_retry_after_on_worker_contention(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api_gateway.cad.routes as routes_mod

    async def _boom(**kwargs: Any) -> dict[str, Any]:
        raise _raised_with_cause(RuntimeError("wrapped"), FreecadWorkerCrashedError("worker died"))

    monkeypatch.setattr(routes_mod, "build_assembly", _boom)
    resp = client.post("/v1/cad/assembly", json={"name": "Bracket", "parts": _ONE_PART})
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "2"
    assert "contention" in resp.json()["detail"].lower()


def test_assembly_returns_502_on_a_non_transient_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api_gateway.cad.routes as routes_mod

    async def _boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("freecad.fillet failed: radius too large")

    monkeypatch.setattr(routes_mod, "build_assembly", _boom)
    resp = client.post("/v1/cad/assembly", json={"name": "Bracket", "parts": _ONE_PART})
    assert resp.status_code == 502
    assert "retry-after" not in resp.headers
