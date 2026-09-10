"""Tests for the CAD conversion API routes."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.server import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def mock_service():
    """Mock the ConversionService used by routes."""
    with patch("api_gateway.convert.routes._service") as _:
        svc = MagicMock()

        # Patch get_service to return our mock
        with patch("api_gateway.convert.routes.get_service", return_value=svc):
            yield svc


class TestUploadAndConvert:
    """POST /v1/convert."""

    @pytest.mark.anyio
    async def test_rejects_unsupported_extension(self, client):
        file = io.BytesIO(b"not a cad file")
        response = await client.post(
            "/v1/convert",
            files={"file": ("model.stl", file, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "Unsupported" in response.json()["detail"]

    @pytest.mark.anyio
    async def test_rejects_empty_file(self, client):
        file = io.BytesIO(b"")
        response = await client.post(
            "/v1/convert",
            files={"file": ("model.step", file, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "Empty" in response.json()["detail"]

    @pytest.mark.anyio
    async def test_successful_conversion(self, client, mock_service):
        mock_service.convert.return_value = {
            "hash": "abc123",
            "glb_url": "/v1/convert/abc123/glb?quality=standard",
            "metadata": {
                "parts": [],
                "materials": [],
                "stats": {"triangleCount": 0, "fileSize": 0},
            },
            "cached": False,
        }

        file = io.BytesIO(b"fake step content")
        response = await client.post(
            "/v1/convert",
            files={"file": ("bracket.step", file, "application/octet-stream")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["hash"] == "abc123"
        assert data["cached"] is False
        mock_service.convert.assert_called_once()

    @pytest.mark.anyio
    async def test_cache_hit(self, client, mock_service):
        mock_service.convert.return_value = {
            "hash": "abc123",
            "glb_url": "/v1/convert/abc123/glb?quality=standard",
            "metadata": {
                "parts": [],
                "materials": [],
                "stats": {"triangleCount": 0, "fileSize": 0},
            },
            "cached": True,
        }

        file = io.BytesIO(b"fake step content")
        response = await client.post(
            "/v1/convert",
            files={"file": ("bracket.step", file, "application/octet-stream")},
        )
        assert response.status_code == 200
        assert response.json()["cached"] is True


class TestGetConversion:
    """GET /v1/convert/{hash}."""

    @pytest.mark.anyio
    async def test_not_found(self, client, mock_service):
        mock_service.get_metadata.return_value = None
        response = await client.get("/v1/convert/nonexistent")
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_found(self, client, mock_service):
        mock_service.get_metadata.return_value = {
            "parts": [{"name": "Part_1", "meshName": "mesh_0", "children": [], "boundingBox": {}}],
            "materials": [],
            "stats": {"triangleCount": 100, "fileSize": 5000},
        }

        response = await client.get("/v1/convert/abc123")
        assert response.status_code == 200
        data = response.json()
        assert data["hash"] == "abc123"
        assert data["cached"] is True


class TestGetGlb:
    """GET /v1/convert/{hash}/glb."""

    @pytest.mark.anyio
    async def test_not_found(self, client, mock_service):
        mock_service.get_glb_path.return_value = None
        response = await client.get("/v1/convert/nonexistent/glb")
        assert response.status_code == 404


class TestGetMetadata:
    """GET /v1/convert/{hash}/metadata."""

    @pytest.mark.anyio
    async def test_not_found(self, client, mock_service):
        mock_service.get_metadata.return_value = None
        response = await client.get("/v1/convert/nonexistent/metadata")
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_returns_metadata(self, client, mock_service):
        meta = {"parts": [], "materials": [], "stats": {"triangleCount": 0, "fileSize": 0}}
        mock_service.get_metadata.return_value = meta

        response = await client.get("/v1/convert/abc123/metadata")
        assert response.status_code == 200
        assert response.json() == meta


class TestEventLoopIsNotBlocked:
    """MET-725: ``ConversionService.convert`` is synchronous and does a
    blocking ``httpx.post`` with a 120s timeout.

    Called directly from an ``async def`` route it held the event loop for the
    entire conversion, so one slow CAD conversion served nothing else at all --
    no ``/health`` (hence containers going ``unhealthy``), no chat, no SSE, and
    no shutdown progress. That is the most likely cause of MET-694's wedge.
    """

    @pytest.mark.anyio
    async def test_a_slow_conversion_does_not_starve_the_loop(self, client, mock_service):
        import asyncio
        import time

        block_seconds = 0.4

        def _slow_blocking_convert(*_args, **_kwargs):
            # A real blocking call, not ``await asyncio.sleep`` -- the defect
            # is specifically that a *synchronous* call cannot yield.
            time.sleep(block_seconds)
            return {
                "hash": "abc123",
                "glb_url": "/v1/convert/abc123/model.glb",
                "metadata": {"parts": [], "stats": {}},
                "cached": False,
            }

        mock_service.convert.side_effect = _slow_blocking_convert

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        try:
            response = await client.post(
                "/v1/convert",
                files={
                    "file": (
                        "bracket.step",
                        io.BytesIO(b"ISO-10303-21;"),
                        "application/octet-stream",
                    )
                },
            )
        finally:
            tick_task.cancel()

        assert response.status_code == 200
        # On the loop this is exactly 0 -- the ticker never gets to run. Off
        # the loop it fires ~40 times in 0.4s; assert well below that so the
        # test is not timing-sensitive on a loaded CI box.
        assert ticks >= 5, f"event loop was starved during the conversion (ticks={ticks})"
