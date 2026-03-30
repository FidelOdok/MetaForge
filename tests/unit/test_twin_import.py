"""Tests for the work product import API (MET-248)."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.twin.import_service import (
    ImportService,
    get_extension,
    infer_domain,
    infer_wp_type,
)
from twin_core.models.enums import WorkProductType

# ---------------------------------------------------------------------------
# Extension helpers
# ---------------------------------------------------------------------------


class TestGetExtension:
    def test_step(self):
        assert get_extension("bracket.step") == ".step"

    def test_stp(self):
        assert get_extension("model.STP") == ".stp"

    def test_kicad_sch(self):
        assert get_extension("drone-fc.kicad_sch") == ".kicad_sch"

    def test_kicad_pcb(self):
        assert get_extension("board.kicad_pcb") == ".kicad_pcb"

    def test_kicad_pro(self):
        assert get_extension("project.kicad_pro") == ".kicad_pro"

    def test_fcstd(self):
        assert get_extension("enclosure.FCStd") == ".fcstd"

    def test_unknown(self):
        assert get_extension("readme.txt") == ".txt"


class TestInferDomain:
    def test_step(self):
        assert infer_domain(".step") == "mechanical"

    def test_kicad_sch(self):
        assert infer_domain(".kicad_sch") == "electronics"

    def test_kicad_pcb(self):
        assert infer_domain(".kicad_pcb") == "electronics"

    def test_fcstd(self):
        assert infer_domain(".fcstd") == "mechanical"


class TestInferWpType:
    def test_step(self):
        assert infer_wp_type(".step") == WorkProductType.CAD_MODEL

    def test_kicad_sch(self):
        assert infer_wp_type(".kicad_sch") == WorkProductType.SCHEMATIC

    def test_kicad_pcb(self):
        assert infer_wp_type(".kicad_pcb") == WorkProductType.PCB_LAYOUT


# ---------------------------------------------------------------------------
# ImportService metadata extraction
# ---------------------------------------------------------------------------


class TestImportServiceMetadata:
    async def test_basic_metadata(self):
        service = ImportService()
        content = b"some file content"
        meta = await service.extract_metadata(content, "unknown.xyz")
        assert meta["source"] == "basic"
        assert meta["file_size"] == len(content)
        assert "content_hash" in meta
        assert meta["original_filename"] == "unknown.xyz"

    async def test_kicad_sch_metadata(self):
        sch_content = (
            b"(kicad_sch (version 20230121)\n"
            b'  (symbol (lib_id "Device:R") (at 100 100))\n'
            b'  (symbol (lib_id "Device:C") (at 200 200))\n'
            b"  (wire (pts (xy 100 100) (xy 200 100)))\n"
            b'  (label "GND")\n'
            b")"
        )
        service = ImportService()
        meta = await service.extract_metadata(sch_content, "test.kicad_sch")
        assert meta["source"] == "kicad_parser"
        assert meta["component_count"] == 2
        assert meta["wire_count"] == 1
        assert meta["label_count"] == 1

    async def test_kicad_pcb_metadata(self):
        pcb_content = (
            b"(kicad_pcb (version 20221018)\n"
            b'  (footprint "R_0402" (at 10 10))\n'
            b'  (footprint "C_0402" (at 20 20))\n'
            b"  (segment (start 10 10) (end 20 20))\n"
            b"  (via (at 15 15))\n"
            b"  (zone (net 0))\n"
            b")"
        )
        service = ImportService()
        meta = await service.extract_metadata(pcb_content, "board.kicad_pcb")
        assert meta["source"] == "kicad_parser"
        assert meta["footprint_count"] == 2
        assert meta["track_count"] == 1
        assert meta["via_count"] == 1
        assert meta["zone_count"] == 1

    async def test_fcstd_no_brep_returns_size_only(self):
        """ZIP with no .brep files returns only format and file_size."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Document.xml", "<Document/>")
            zf.writestr("GuiDocument.xml", "<GuiDocument/>")
        content = buf.getvalue()

        service = ImportService()
        meta = await service.extract_metadata(content, "part.FCStd")
        assert meta["format"] == "fcstd"
        assert meta["file_size"] == len(content)
        # No brep → no part_count, no geometry fields
        assert "part_count" not in meta
        assert "source" not in meta or meta.get("source") != "fcstd_parser"

    async def test_fcstd_with_brep_returns_part_count(self):
        """ZIP with 2 .brep files sets part_count == 2."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Document.xml", "<Document/>")
            zf.writestr("Shape1.brep", b"BRep body 1 data")
            zf.writestr("Shape2.brep", b"BRep body 2 larger data here")
        content = buf.getvalue()

        mock_response = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "metadata": {"parts": [], "stats": {}}
        }

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            service = ImportService()
            meta = await service.extract_metadata(content, "enclosure.FCStd")

        assert meta["format"] == "fcstd"
        assert meta["part_count"] == 2

    async def test_fcstd_with_brep_calls_occt(self):
        """OCCT geometry fields are merged into the result."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Shape.brep", b"BRep data")
        content = buf.getvalue()

        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "metadata": {
                "parts": [
                    {"name": "Body", "bounding_box": {"min_x": 0.0, "max_x": 100.0}},
                ],
                "stats": {"triangle_count": 800, "vertex_count": 400},
            }
        }

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            service = ImportService()
            meta = await service.extract_metadata(content, "part.FCStd")

        assert meta["format"] == "fcstd"
        assert meta["part_count"] == 1
        assert meta["source"] == "occt_converter"
        assert meta["triangle_count"] == 800
        assert meta["vertex_count"] == 400
        assert meta["bounding_box"] == {"min_x": 0.0, "max_x": 100.0}

    async def test_fcstd_invalid_zip_falls_back(self):
        """Non-ZIP bytes return {format, file_size} without raising."""
        content = b"this is not a zip file at all"
        service = ImportService()
        meta = await service.extract_metadata(content, "bad.FCStd")
        assert meta["format"] == "fcstd"
        assert meta["file_size"] == len(content)
        assert "part_count" not in meta

    async def test_fcstd_occt_unavailable_falls_back(self):
        """When OCCT raises, result still has format, file_size, and part_count."""
        import httpx as _httpx

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Shape.brep", b"BRep data")
        content = buf.getvalue()

        mock_client = AsyncMock()
        mock_client.post.side_effect = _httpx.ConnectError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            service = ImportService()
            meta = await service.extract_metadata(content, "part.FCStd")

        assert meta["format"] == "fcstd"
        assert meta["file_size"] == len(content)
        assert meta["part_count"] == 1
        # No OCCT fields
        assert "source" not in meta or meta.get("source") != "occt_converter"

    async def test_step_fallback_when_occt_unavailable(self):
        """ConnectError should fall back to basic metadata."""
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.post.side_effect = _httpx.ConnectError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            service = ImportService()
            meta = await service.extract_metadata(b"STEP content", "model.step")
        assert meta["source"] == "basic"
        assert meta["file_size"] == 12

    async def test_step_metadata_with_occt(self):
        """When OCCT is available, extract rich metadata."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "metadata": {
                "parts": [
                    {"name": "Body", "bounding_box": {"min_x": 0, "max_x": 50}},
                    {"name": "Hole"},
                ],
                "stats": {"triangle_count": 1200, "vertex_count": 600},
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            service = ImportService()
            meta = await service.extract_metadata(b"STEP data", "bracket.step")

        assert meta["source"] == "occt_converter"
        assert meta["part_count"] == 2
        assert meta["part_names"] == ["Body", "Hole"]
        assert meta["triangle_count"] == 1200


# ---------------------------------------------------------------------------
# Import endpoint (integration-style)
# ---------------------------------------------------------------------------


class TestImportEndpoint:
    """Test the /v1/twin/import endpoint via ASGI test client."""

    @pytest.fixture(autouse=True)
    def _mock_storage(self):
        """Mock file storage to avoid filesystem writes."""
        with patch("api_gateway.twin.routes.default_storage") as mock_st:
            mock_st.save.return_value = "/tmp/test/file.step"
            mock_st.content_hash.return_value = "abc123"
            yield mock_st

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.twin.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def test_rejects_unsupported_extension(self, client):
        async with client:
            resp = await client.post(
                "/v1/twin/import",
                files={"file": ("model.stl", b"solid data", "application/octet-stream")},
            )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    async def test_rejects_empty_file(self, client):
        async with client:
            resp = await client.post(
                "/v1/twin/import",
                files={"file": ("model.step", b"", "application/octet-stream")},
            )
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"]

    async def test_rejects_invalid_wp_type(self, client):
        async with client:
            resp = await client.post(
                "/v1/twin/import",
                files={"file": ("model.step", b"STEP data", "application/octet-stream")},
                data={"wp_type": "invalid_type"},
            )
        assert resp.status_code == 400
        assert "Invalid wp_type" in resp.json()["detail"]

    async def test_imports_step_file(self, client):
        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={"source": "basic", "file_size": 9},
        ):
            async with client:
                resp = await client.post(
                    "/v1/twin/import",
                    files={"file": ("bracket.step", b"STEP data", "application/octet-stream")},
                    data={"description": "Test bracket"},
                )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Test bracket"
        assert body["domain"] == "mechanical"
        assert body["wp_type"] == "cad_model"
        assert body["format"] == "step"
        assert body["id"]  # UUID present

    async def test_imports_kicad_schematic(self, client):
        sch = b'(kicad_sch (version 20230121) (symbol (lib_id "R")))'
        async with client:
            resp = await client.post(
                "/v1/twin/import",
                files={"file": ("drone.kicad_sch", sch, "application/octet-stream")},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["domain"] == "electronics"
        assert body["wp_type"] == "schematic"
        assert body["format"] == "kicad_sch"

    async def test_custom_domain_overrides_inference(self, client):
        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={"source": "basic", "file_size": 5},
        ):
            async with client:
                resp = await client.post(
                    "/v1/twin/import",
                    files={"file": ("model.step", b"STEP", "application/octet-stream")},
                    data={"domain": "simulation", "wp_type": "simulation_result"},
                )
        assert resp.status_code == 201
        body = resp.json()
        assert body["domain"] == "simulation"
        assert body["wp_type"] == "simulation_result"

    async def test_name_defaults_to_filename_stem(self, client):
        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={"source": "basic", "file_size": 5},
        ):
            async with client:
                resp = await client.post(
                    "/v1/twin/import",
                    files={"file": ("my_bracket.step", b"STEP", "application/octet-stream")},
                )
        assert resp.status_code == 201
        assert resp.json()["name"] == "my_bracket"
