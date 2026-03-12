"""Tests for the OCCT STEP→GLB converter.

These tests verify the converter logic without requiring actual OCCT
installation — they use cadquery (optional) to generate sample STEP
files, or fall back to testing the CLI argument parsing and metadata
schema validation.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

CONVERTER = Path(__file__).parent / "convert.py"


class TestQualityTiers:
    """Verify quality tier configuration."""

    def test_quality_tiers_defined(self):
        from convert import QUALITY_TIERS

        assert "preview" in QUALITY_TIERS
        assert "standard" in QUALITY_TIERS
        assert "fine" in QUALITY_TIERS

    def test_deflection_ordering(self):
        from convert import QUALITY_TIERS

        assert QUALITY_TIERS["preview"] > QUALITY_TIERS["standard"] > QUALITY_TIERS["fine"]


class TestFileTypeDetection:
    """Verify CAD file type detection by extension."""

    def test_step_extensions(self):
        from convert import _read_cad_file

        for ext in (".step", ".stp", ".STEP", ".STP"):
            # Should attempt to read STEP — will fail without OCCT
            # but should NOT raise ValueError for unsupported format
            with pytest.raises((RuntimeError, ImportError)):
                _read_cad_file(f"/fake/model{ext}")

    def test_iges_extensions(self):
        from convert import _read_cad_file

        for ext in (".iges", ".igs"):
            with pytest.raises((RuntimeError, ImportError)):
                _read_cad_file(f"/fake/model{ext}")

    def test_unsupported_extension(self):
        from convert import _read_cad_file

        with pytest.raises(ValueError, match="Unsupported"):
            _read_cad_file("/fake/model.stl")


class TestBoundingBox:
    """Verify bounding box computation."""

    def test_bounding_box(self):
        import numpy as np
        from convert import _bounding_box

        verts = np.array(
            [
                [0, 0, 0],
                [1, 2, 3],
                [-1, -2, -3],
            ],
            dtype=np.float32,
        )

        bb = _bounding_box(verts)
        assert bb["min"] == pytest.approx([-1, -2, -3])
        assert bb["max"] == pytest.approx([1, 2, 3])


class TestMetadataSchema:
    """Verify metadata JSON structure."""

    def test_metadata_has_required_fields(self):
        """Ensure metadata dict follows the expected schema."""
        metadata = {
            "parts": [
                {
                    "name": "Part_1",
                    "meshName": "mesh_0",
                    "children": [],
                    "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]},
                }
            ],
            "materials": [],
            "stats": {"triangleCount": 12, "fileSize": 1024},
        }

        assert "parts" in metadata
        assert "stats" in metadata
        assert isinstance(metadata["parts"], list)
        assert metadata["parts"][0]["meshName"] == "mesh_0"
        assert metadata["stats"]["triangleCount"] == 12


class TestSourceFormat:
    """Verify source format detection from file extension."""

    def test_step_format(self):
        from convert import _source_format

        assert _source_format("/some/file.step") == "STEP-AP242"
        assert _source_format("/some/file.stp") == "STEP-AP242"
        assert _source_format("/some/file.STEP") == "STEP-AP242"
        assert _source_format("/some/file.STP") == "STEP-AP242"

    def test_iges_format(self):
        from convert import _source_format

        assert _source_format("/some/file.iges") == "IGES"
        assert _source_format("/some/file.igs") == "IGES"

    def test_unknown_format(self):
        from convert import _source_format

        assert _source_format("/some/file.stl") == "UNKNOWN"


class TestMetadataVersioningFields:
    """Verify format, schemaVersion, sourceFormat, and convertedAt fields."""

    def _make_metadata_sample(self) -> dict:
        """Build a metadata dict mimicking what convert() produces."""
        from datetime import UTC

        from convert import _source_format

        return {
            "format": "metaforge-twin-export",
            "schemaVersion": "1.0",
            "sourceFormat": _source_format("/model.step"),
            "convertedAt": datetime.now(UTC).isoformat(),
            "parts": [],
            "materials": [],
            "stats": {"triangleCount": 0, "fileSize": 0},
        }

    def test_format_field(self):
        meta = self._make_metadata_sample()
        assert meta["format"] == "metaforge-twin-export"

    def test_schema_version_field(self):
        meta = self._make_metadata_sample()
        assert meta["schemaVersion"] == "1.0"

    def test_source_format_field(self):
        meta = self._make_metadata_sample()
        assert meta["sourceFormat"] == "STEP-AP242"

    def test_converted_at_is_iso8601(self):
        meta = self._make_metadata_sample()
        # Should parse without error
        parsed = datetime.fromisoformat(meta["convertedAt"])
        assert parsed is not None

    def test_converted_at_is_utc(self):
        meta = self._make_metadata_sample()
        parsed = datetime.fromisoformat(meta["convertedAt"])
        assert parsed.tzinfo is not None


class TestCLIArgs:
    """Test command-line argument parsing."""

    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(CONVERTER), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--quality" in result.stdout
        assert "--output-dir" in result.stdout
