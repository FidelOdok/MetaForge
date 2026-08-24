"""Comprehensive tests for the STEP-to-GLB conversion REST API (MET-152).

Covers all four endpoints, content-hash caching, quality parameter validation,
SHA-256 computation, ConversionService cache behaviour, and graceful fallback
when the OCCT Docker container is unavailable.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.convert.schemas import (
    BoundingBox,
    ConversionJob,
    ConversionResult,
    ConversionStatus,
    ModelStats,
    PartTreeMetadata,
    PartTreeNode,
    QualityLevel,
)
from api_gateway.convert.service import ConversionService
from api_gateway.server import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STEP_CONTENT = b"ISO-10303-21; HEADER; ... END-ISO-10303-21;"


@pytest.fixture()
def app():
    """Create a fresh gateway app for testing."""
    return create_app()


@pytest.fixture()
async def client(app):
    """Async HTTP client bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def mock_service():
    """Patch the ConversionService used by the routes module."""
    with patch("api_gateway.convert.routes._service"):
        svc = MagicMock(spec=ConversionService)
        with patch("api_gateway.convert.routes.get_service", return_value=svc):
            yield svc


@pytest.fixture()
def tmp_cache(tmp_path: Path) -> Path:
    """Temporary cache directory for ConversionService."""
    cache = tmp_path / "convert-cache"
    cache.mkdir()
    return cache


@pytest.fixture()
def service(tmp_cache: Path) -> ConversionService:
    """ConversionService backed by a temporary cache directory."""
    return ConversionService(cache_dir=tmp_cache, occt_url="http://fake-occt:9999")


# ---------------------------------------------------------------------------
# Schema model tests
# ---------------------------------------------------------------------------


class TestSchemaModels:
    """Validate Pydantic schema models."""

    def test_conversion_status_values(self) -> None:
        assert ConversionStatus.PENDING == "pending"
        assert ConversionStatus.PROCESSING == "processing"
        assert ConversionStatus.COMPLETED == "completed"
        assert ConversionStatus.FAILED == "failed"

    def test_quality_level_values(self) -> None:
        assert QualityLevel.PREVIEW == "preview"
        assert QualityLevel.STANDARD == "standard"
        assert QualityLevel.FINE == "fine"

    def test_part_tree_node_defaults(self) -> None:
        node = PartTreeNode(name="Part_1")
        assert node.name == "Part_1"
        assert node.mesh_name == ""
        assert node.children == []
        assert isinstance(node.bounding_box, BoundingBox)

    def test_part_tree_node_nested(self) -> None:
        child = PartTreeNode(name="Child")
        parent = PartTreeNode(name="Assembly", children=[child])
        assert len(parent.children) == 1
        assert parent.children[0].name == "Child"

    def test_model_stats_defaults(self) -> None:
        stats = ModelStats()
        assert stats.triangle_count == 0
        assert stats.file_size == 0
        assert stats.vertex_count == 0

    def test_part_tree_metadata_roundtrip(self) -> None:
        meta = PartTreeMetadata(
            parts=[PartTreeNode(name="Top")],
            stats=ModelStats(triangle_count=100, file_size=5000),
        )
        data = meta.model_dump()
        restored = PartTreeMetadata.model_validate(data)
        assert restored.parts[0].name == "Top"
        assert restored.stats.triangle_count == 100

    def test_conversion_result_model(self) -> None:
        result = ConversionResult(
            hash="abc123",
            glb_url="/v1/convert/abc123/glb",
            metadata={"parts": []},
            cached=False,
        )
        assert result.hash == "abc123"
        assert result.cached is False

    def test_conversion_job_default_status(self) -> None:
        job = ConversionJob(job_id="j-1")
        assert job.status == ConversionStatus.PENDING
        assert job.result is None


# ---------------------------------------------------------------------------
# ConversionService unit tests
# ---------------------------------------------------------------------------


class TestConversionService:
    """Unit tests for ConversionService (cache and hashing)."""

    def test_sha256_hash_computation(self, service: ConversionService) -> None:
        expected = hashlib.sha256(STEP_CONTENT).hexdigest()
        assert service.content_hash(STEP_CONTENT) == expected

    def test_sha256_deterministic(self, service: ConversionService) -> None:
        h1 = service.content_hash(STEP_CONTENT)
        h2 = service.content_hash(STEP_CONTENT)
        assert h1 == h2

    def test_sha256_different_content(self, service: ConversionService) -> None:
        h1 = service.content_hash(b"content-a")
        h2 = service.content_hash(b"content-b")
        assert h1 != h2

    def test_cache_miss_returns_none(self, service: ConversionService) -> None:
        assert service.get_cached("nonexistent", "standard") is None

    def test_cache_hit_after_write(self, service: ConversionService) -> None:
        file_hash = service.content_hash(STEP_CONTENT)
        cache = service._cache_path(file_hash, "standard")
        cache.mkdir(parents=True, exist_ok=True)
        metadata = {"parts": [], "materials": [], "stats": {}}
        (cache / "metadata.json").write_text(json.dumps(metadata))
        (cache / "model.glb").write_bytes(b"\x00glb-data")

        result = service.get_cached(file_hash, "standard")
        assert result is not None
        assert result["parts"] == []

    def test_get_glb_path_not_found(self, service: ConversionService) -> None:
        assert service.get_glb_path("missing", "standard") is None

    def test_get_glb_path_found(self, service: ConversionService) -> None:
        file_hash = "abc123"
        cache = service._cache_path(file_hash, "standard")
        cache.mkdir(parents=True, exist_ok=True)
        glb = cache / "model.glb"
        glb.write_bytes(b"\x00glb")

        path = service.get_glb_path(file_hash, "standard")
        assert path is not None
        assert path == glb

    def test_get_metadata_not_found(self, service: ConversionService) -> None:
        assert service.get_metadata("missing", "standard") is None

    def test_get_metadata_found(self, service: ConversionService) -> None:
        file_hash = "abc123"
        cache = service._cache_path(file_hash, "standard")
        cache.mkdir(parents=True, exist_ok=True)
        meta = {"parts": [{"name": "P1"}], "materials": [], "stats": {}}
        (cache / "metadata.json").write_text(json.dumps(meta))

        result = service.get_metadata(file_hash, "standard")
        assert result is not None
        assert result["parts"][0]["name"] == "P1"

    def test_convert_cache_hit(self, service: ConversionService) -> None:
        """convert() returns cached=True when cache has the result."""
        file_hash = service.content_hash(STEP_CONTENT)
        cache = service._cache_path(file_hash, "standard")
        cache.mkdir(parents=True, exist_ok=True)
        meta = {"parts": [], "materials": [], "stats": {}}
        (cache / "metadata.json").write_text(json.dumps(meta))
        (cache / "model.glb").write_bytes(b"\x00glb")

        result = service.convert(STEP_CONTENT, "test.step", "standard")
        assert result["cached"] is True
        assert result["hash"] == file_hash

    def test_convert_calls_occt_on_miss(self, service: ConversionService) -> None:
        """convert() invokes _call_occt_service on a cache miss."""
        with patch.object(service, "_call_occt_service") as mock_call:
            # The method writes files to the cache dir; simulate that.
            def _fake_call(file_bytes: bytes, filename: str, quality: str, cache: Path) -> None:
                cache.mkdir(parents=True, exist_ok=True)
                meta = {"parts": [], "materials": [], "stats": {}}
                (cache / "metadata.json").write_text(json.dumps(meta))
                (cache / "model.glb").write_bytes(b"\x00glb")

            mock_call.side_effect = _fake_call
            result = service.convert(STEP_CONTENT, "bracket.step", "standard")
            assert result["cached"] is False
            mock_call.assert_called_once()

    def test_convert_quality_param_forwarded(self, service: ConversionService) -> None:
        """Quality parameter is passed through to the cache path."""
        with patch.object(service, "_call_occt_service") as mock_call:

            def _fake_call(file_bytes: bytes, filename: str, quality: str, cache: Path) -> None:
                cache.mkdir(parents=True, exist_ok=True)
                (cache / "metadata.json").write_text(json.dumps({"parts": []}))
                (cache / "model.glb").write_bytes(b"\x00")

            mock_call.side_effect = _fake_call
            result = service.convert(STEP_CONTENT, "test.step", "fine")
            assert "quality=fine" in result["glb_url"]

    def test_fallback_when_occt_unavailable(self, service: ConversionService) -> None:
        """Service produces fallback metadata when OCCT container cannot be reached."""
        import httpx

        with patch("api_gateway.convert.service.httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")
            result = service.convert(STEP_CONTENT, "bracket.step", "standard")
            assert result["cached"] is False
            assert result["metadata"]["converter_unavailable"] is True
            assert result["metadata"]["parts"][0]["name"] == "bracket"

    def test_occt_rejection_raises_conversion_error(self, service: ConversionService) -> None:
        """MET-652: a non-200 OCCT response raises the typed ConversionError
        (not a bare RuntimeError), carrying the status code and body so
        callers can distinguish "bad content" from "server crashed"."""
        from api_gateway.convert.service import ConversionError

        with patch("api_gateway.convert.service.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=500, text='{"error": "Can\'t export empty scenes!"}'
            )
            with pytest.raises(ConversionError) as exc_info:
                service.convert(STEP_CONTENT, "empty.step", "standard")
            assert exc_info.value.status_code == 500
            assert "empty scenes" in exc_info.value.body


# ---------------------------------------------------------------------------
# Route endpoint tests — POST /v1/convert
# ---------------------------------------------------------------------------


class TestPostConvert:
    """POST /v1/convert endpoint."""

    @pytest.mark.anyio
    async def test_valid_step_upload(self, client, mock_service) -> None:
        mock_service.convert.return_value = {
            "hash": "abc123",
            "glb_url": "/v1/convert/abc123/glb?quality=standard",
            "metadata": {"parts": [], "materials": [], "stats": {}},
            "cached": False,
        }
        file = io.BytesIO(STEP_CONTENT)
        resp = await client.post(
            "/v1/convert",
            files={"file": ("bracket.step", file, "application/octet-stream")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hash"] == "abc123"
        assert data["cached"] is False
        mock_service.convert.assert_called_once()

    @pytest.mark.anyio
    async def test_cache_hit_returns_cached(self, client, mock_service) -> None:
        mock_service.convert.return_value = {
            "hash": "abc123",
            "glb_url": "/v1/convert/abc123/glb?quality=standard",
            "metadata": {"parts": []},
            "cached": True,
        }
        file = io.BytesIO(STEP_CONTENT)
        resp = await client.post(
            "/v1/convert",
            files={"file": ("bracket.step", file, "application/octet-stream")},
        )
        assert resp.status_code == 200
        assert resp.json()["cached"] is True

    @pytest.mark.anyio
    async def test_rejects_unsupported_extension(self, client) -> None:
        file = io.BytesIO(b"not a cad file")
        resp = await client.post(
            "/v1/convert",
            files={"file": ("model.stl", file, "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_rejects_empty_file(self, client) -> None:
        file = io.BytesIO(b"")
        resp = await client.post(
            "/v1/convert",
            files={"file": ("model.step", file, "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "Empty" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_quality_parameter_preview(self, client, mock_service) -> None:
        mock_service.convert.return_value = {
            "hash": "h1",
            "glb_url": "/v1/convert/h1/glb?quality=preview",
            "metadata": {"parts": []},
            "cached": False,
        }
        file = io.BytesIO(STEP_CONTENT)
        resp = await client.post(
            "/v1/convert?quality=preview",
            files={"file": ("bracket.step", file, "application/octet-stream")},
        )
        assert resp.status_code == 200
        # The service was called with quality=preview
        call_args = mock_service.convert.call_args
        assert call_args[0][2] == "preview"

    @pytest.mark.anyio
    async def test_quality_parameter_invalid(self, client) -> None:
        file = io.BytesIO(STEP_CONTENT)
        resp = await client.post(
            "/v1/convert?quality=ultra",
            files={"file": ("bracket.step", file, "application/octet-stream")},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_accepts_iges_extension(self, client, mock_service) -> None:
        mock_service.convert.return_value = {
            "hash": "xyz",
            "glb_url": "/v1/convert/xyz/glb?quality=standard",
            "metadata": {"parts": []},
            "cached": False,
        }
        file = io.BytesIO(b"iges-data")
        resp = await client.post(
            "/v1/convert",
            files={"file": ("model.iges", file, "application/octet-stream")},
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_accepts_stp_extension(self, client, mock_service) -> None:
        mock_service.convert.return_value = {
            "hash": "xyz",
            "glb_url": "/v1/convert/xyz/glb?quality=standard",
            "metadata": {"parts": []},
            "cached": False,
        }
        file = io.BytesIO(b"stp-data")
        resp = await client.post(
            "/v1/convert",
            files={"file": ("model.stp", file, "application/octet-stream")},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Route endpoint tests — GET /v1/convert/{hash}
# ---------------------------------------------------------------------------


class TestGetConversion:
    """GET /v1/convert/{hash} endpoint."""

    @pytest.mark.anyio
    async def test_returns_status_when_found(self, client, mock_service) -> None:
        mock_service.get_metadata.return_value = {
            "parts": [{"name": "P1"}],
            "materials": [],
            "stats": {"triangleCount": 42},
        }
        resp = await client.get("/v1/convert/abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hash"] == "abc123"
        assert data["cached"] is True

    @pytest.mark.anyio
    async def test_not_found(self, client, mock_service) -> None:
        mock_service.get_metadata.return_value = None
        resp = await client.get("/v1/convert/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Route endpoint tests — GET /v1/convert/{hash}/glb
# ---------------------------------------------------------------------------


class TestGetGlb:
    """GET /v1/convert/{hash}/glb endpoint."""

    @pytest.mark.anyio
    async def test_not_found(self, client, mock_service) -> None:
        mock_service.get_glb_path.return_value = None
        resp = await client.get("/v1/convert/nonexistent/glb")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_returns_file(self, client, mock_service, tmp_path) -> None:
        glb_file = tmp_path / "model.glb"
        glb_file.write_bytes(b"\x00\x01\x02glTF")
        mock_service.get_glb_path.return_value = glb_file
        resp = await client.get("/v1/convert/abc123/glb")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "model/gltf-binary"


# ---------------------------------------------------------------------------
# Route endpoint tests — GET /v1/convert/{hash}/metadata
# ---------------------------------------------------------------------------


class TestGetMetadata:
    """GET /v1/convert/{hash}/metadata endpoint."""

    @pytest.mark.anyio
    async def test_not_found(self, client, mock_service) -> None:
        mock_service.get_metadata.return_value = None
        resp = await client.get("/v1/convert/nonexistent/metadata")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_returns_metadata_json(self, client, mock_service) -> None:
        meta = {"parts": [{"name": "P1"}], "materials": [], "stats": {"triangleCount": 10}}
        mock_service.get_metadata.return_value = meta
        resp = await client.get("/v1/convert/abc123/metadata")
        assert resp.status_code == 200
        assert resp.json() == meta
