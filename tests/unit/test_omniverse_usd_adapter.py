"""Tests for the OpenUSD conversion MCP tool adapter (MET-634)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tool_registry.tools.omniverse_usd.adapter import OmniverseUsdServer
from tool_registry.tools.omniverse_usd.config import OmniverseUsdConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> OmniverseUsdServer:
    """Bare adapter (no mocks on converter functions)."""
    return OmniverseUsdServer()


# ---------------------------------------------------------------------------
# TestOmniverseUsdConfig
# ---------------------------------------------------------------------------


class TestOmniverseUsdConfig:
    def test_default_config(self) -> None:
        cfg = OmniverseUsdConfig()
        assert cfg.work_dir == "/tmp/omniverse_usd"
        assert cfg.default_meters_per_unit == 0.001
        assert cfg.stage_format == ".usda"

    def test_custom_config(self) -> None:
        cfg = OmniverseUsdConfig(
            work_dir="/data/usd", default_meters_per_unit=1.0, stage_format=".usdc"
        )
        assert cfg.work_dir == "/data/usd"
        assert cfg.default_meters_per_unit == 1.0
        assert cfg.stage_format == ".usdc"

    def test_non_positive_meters_per_unit_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            OmniverseUsdConfig(default_meters_per_unit=0.0)


# ---------------------------------------------------------------------------
# TestOmniverseUsdServer
# ---------------------------------------------------------------------------


class TestOmniverseUsdServer:
    def test_server_registers_three_tools(self, server: OmniverseUsdServer) -> None:
        assert len(server.tool_ids) == 3

    def test_tool_ids(self, server: OmniverseUsdServer) -> None:
        expected = {
            "omniverse_usd.convert_glb_to_usd",
            "omniverse_usd.validate_usd_minimum",
            "omniverse_usd.describe_stage",
        }
        assert set(server.tool_ids) == expected

    def test_adapter_id_and_version(self, server: OmniverseUsdServer) -> None:
        assert server.adapter_id == "omniverse_usd"
        assert server.version == "0.1.0"

    def test_custom_config_propagated(self) -> None:
        cfg = OmniverseUsdConfig(work_dir="/custom")
        s = OmniverseUsdServer(config=cfg)
        assert s.config.work_dir == "/custom"


# ---------------------------------------------------------------------------
# TestConvertGlbToUsd (dispatch + validation, converter mocked)
# ---------------------------------------------------------------------------


class TestConvertGlbToUsd:
    async def test_success(self, server: OmniverseUsdServer) -> None:
        with patch(
            "tool_registry.tools.omniverse_usd.adapter.converter_convert_glb_to_usd",
            return_value={
                "output_path": "/tmp/out.usda",
                "prim_count": 3,
                "mesh_count": 2,
                "part_names": ["bracket_body", "bracket_mount"],
            },
        ) as mock_convert:
            result = await server.convert_glb_to_usd(
                {"glb_path": "/tmp/model.glb", "output_path": "/tmp/out.usda"}
            )

        assert result["mesh_count"] == 2
        assert result["part_names"] == ["bracket_body", "bracket_mount"]
        mock_convert.assert_called_once()

    async def test_missing_glb_path_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(ValueError, match="glb_path is required"):
            await server.convert_glb_to_usd({"glb_path": "", "output_path": "/tmp/out.usda"})

    async def test_missing_output_path_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(ValueError, match="output_path is required"):
            await server.convert_glb_to_usd({"glb_path": "/tmp/model.glb", "output_path": ""})

    async def test_default_meters_per_unit_used(self, server: OmniverseUsdServer) -> None:
        with patch(
            "tool_registry.tools.omniverse_usd.adapter.converter_convert_glb_to_usd",
            return_value={"output_path": "x", "prim_count": 1, "mesh_count": 0, "part_names": []},
        ) as mock_convert:
            await server.convert_glb_to_usd(
                {"glb_path": "/tmp/model.glb", "output_path": "/tmp/out.usda"}
            )
        _, kwargs = mock_convert.call_args
        assert kwargs["meters_per_unit"] == 0.001


# ---------------------------------------------------------------------------
# TestValidateUsdMinimum
# ---------------------------------------------------------------------------


class TestValidateUsdMinimum:
    async def test_success(self, server: OmniverseUsdServer) -> None:
        with patch(
            "tool_registry.tools.omniverse_usd.adapter.converter_validate_usd_minimum",
            return_value={
                "valid": True,
                "mesh_count": 2,
                "has_default_prim": True,
                "meters_per_unit": 0.001,
                "issues": [],
            },
        ):
            result = await server.validate_usd_minimum({"usd_path": "/tmp/stage.usda"})
        assert result["valid"] is True

    async def test_missing_usd_path_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(ValueError, match="usd_path is required"):
            await server.validate_usd_minimum({"usd_path": ""})


# ---------------------------------------------------------------------------
# TestDescribeStage
# ---------------------------------------------------------------------------


class TestDescribeStage:
    async def test_success(self, server: OmniverseUsdServer) -> None:
        with patch(
            "tool_registry.tools.omniverse_usd.adapter.converter_describe_stage",
            return_value={
                "up_axis": "Z",
                "meters_per_unit": 0.001,
                "prim_paths": ["/Root", "/Root/bracket_body"],
                "mesh_count": 1,
            },
        ):
            result = await server.describe_stage({"usd_path": "/tmp/stage.usda"})
        assert result["mesh_count"] == 1

    async def test_missing_usd_path_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(ValueError, match="usd_path is required"):
            await server.describe_stage({"usd_path": ""})


# ---------------------------------------------------------------------------
# TestUnmockedConverterRaisesOnMissingFiles
# ---------------------------------------------------------------------------


class TestUnmockedConverterRaisesOnMissingFiles:
    """Verify that calling adapter methods without mocks raises on missing files."""

    async def test_convert_glb_to_usd_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server.convert_glb_to_usd(
                {"glb_path": "/nonexistent/model.glb", "output_path": "/tmp/out.usda"}
            )

    async def test_validate_usd_minimum_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server.validate_usd_minimum({"usd_path": "/nonexistent/stage.usda"})

    async def test_describe_stage_raises(self, server: OmniverseUsdServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server.describe_stage({"usd_path": "/nonexistent/stage.usda"})
