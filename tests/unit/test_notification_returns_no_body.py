"""A 204 must not carry a body (FORGE-422).

The sidecar returned `JSONResponse(content=None, status_code=204)` for a
JSON-RPC notification. `JSONResponse` serialises `None` to `b"null"` -- four
bytes on a status that MUST have none -- so uvicorn sets `Content-Length: 0`
and then raises:

    RuntimeError: Response content longer than Content-Length

The client still received its 204, so it looked fine from outside while the
server logged an ASGI traceback. `notifications/initialized` is the first
thing a spec-compliant client sends after `initialize`, so this fired on
every connection; four were in the sidecar log during one plugin run.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient


class TestTheResponseShapeItself:
    def test_a_json_response_of_none_is_not_empty(self) -> None:
        """The whole bug in one assertion: `content=None` is not "no content"."""
        assert JSONResponse(content=None, status_code=204).body == b"null"

    def test_a_bare_response_is(self) -> None:
        assert Response(status_code=204).body == b""


def test_the_sidecar_can_still_serve_a_204(tmp_path: Any) -> None:
    """Through a real ASGI stack, so the response is actually *sent* and not
    merely constructed.

    What this deliberately does **not** claim: that the old shape raises here.
    starlette's TestClient is more permissive than uvicorn's httptools
    implementation and sends `b"null"` on a 204 without complaint -- which is
    exactly why the bug reached production. The contradiction is enforced in
    uvicorn, and a test asserting it through a transport that does not enforce
    it would be proving something else and calling it this.
    """
    app = FastAPI()

    @app.post("/probe")
    async def probe() -> Response:
        return Response(status_code=204)

    response = TestClient(app).post("/probe")
    assert response.status_code == 204
    assert response.content == b""


class TestTheSidecarUsesTheEmptyOne:
    def test_no_json_response_carries_a_204(self) -> None:
        """Source-level, because both sites are one line each and the next
        person adding a 204 should land on the right one."""
        from pathlib import Path

        source = Path("metaforge/mcp/__main__.py").read_text()
        assert "JSONResponse(content=None, status_code=204)" not in source
        assert source.count("Response(status_code=204)") >= 2
