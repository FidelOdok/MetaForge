"""Unit tests for make_manufacture_release / twin.manufacture_release
(FORGE-294)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from api_gateway.twin.manufacture_release import (
    PROCESS_EXPORT_FORMATS,
    make_manufacture_release,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _FakeBlobStager:
    def __init__(self, file_path: str) -> None:
        self.calls: list[str] = []
        self.file_path = file_path

    async def __call__(self, node_id: str) -> dict[str, Any]:
        self.calls.append(node_id)
        return {
            "node_id": node_id,
            "file_path": self.file_path,
            "filename": Path(self.file_path).name,
            "size_bytes": 123,
            "content_hash": "deadbeef",
            "format": "step",
        }


class _FakeBridge:
    def __init__(self, output_file: str, output_content: bytes) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.output_file = output_file
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_bytes(output_content)

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id == "cadquery.export_geometry":
            return {
                "output_file": self.output_file,
                "file_size_bytes": Path(self.output_file).stat().st_size,
                "format": params["output_format"],
            }
        raise AssertionError(f"unexpected tool call: {tool_id}")


async def _seed_wp(twin: InMemoryTwinAPI, *, format_: str = "step") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name="upper_arm",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format=format_,
            created_by="test",
        )
    )


class TestManufactureRelease:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI, tmp_path: Path) -> None:
        release = make_manufacture_release(
            twin,
            blob_stager=_FakeBlobStager(str(tmp_path / "in.step")),
            mcp_bridge=_FakeBridge(str(tmp_path / "out.stl"), b"fake stl"),
            workspace_dir=tmp_path,
        )
        with pytest.raises(ValueError, match="no work_product"):
            await release(
                work_product_id="11111111-1111-1111-1111-111111111111",
                process="3d_print",
            )

    async def test_invalid_work_product_id_raises(
        self, twin: InMemoryTwinAPI, tmp_path: Path
    ) -> None:
        release = make_manufacture_release(
            twin,
            blob_stager=_FakeBlobStager(str(tmp_path / "in.step")),
            mcp_bridge=_FakeBridge(str(tmp_path / "out.stl"), b"fake stl"),
            workspace_dir=tmp_path,
        )
        with pytest.raises(ValueError, match="invalid work_product_id"):
            await release(work_product_id="not-a-uuid", process="3d_print")

    async def test_unknown_process_raises(self, twin: InMemoryTwinAPI, tmp_path: Path) -> None:
        wp = await _seed_wp(twin)
        release = make_manufacture_release(
            twin,
            blob_stager=_FakeBlobStager(str(tmp_path / "in.step")),
            mcp_bridge=_FakeBridge(str(tmp_path / "out.stl"), b"fake stl"),
            workspace_dir=tmp_path,
        )
        with pytest.raises(ValueError, match="unknown process"):
            await release(work_product_id=str(wp.id), process="sheet_metal")

    async def test_non_step_source_format_rejected(
        self, twin: InMemoryTwinAPI, tmp_path: Path
    ) -> None:
        wp = await _seed_wp(twin, format_="stl")
        release = make_manufacture_release(
            twin,
            blob_stager=_FakeBlobStager(str(tmp_path / "in.stl")),
            mcp_bridge=_FakeBridge(str(tmp_path / "out.stl"), b"fake stl"),
            workspace_dir=tmp_path,
        )
        with pytest.raises(ValueError, match="expected a STEP file"):
            await release(work_product_id=str(wp.id), process="3d_print")

    async def test_3d_print_process_exports_stl_and_stages_real_file(
        self, twin: InMemoryTwinAPI, tmp_path: Path
    ) -> None:
        wp = await _seed_wp(twin)
        stager = _FakeBlobStager(str(tmp_path / "staged" / "upper_arm.step"))
        stl_bytes = b"solid demo\nendsolid demo\n"
        bridge = _FakeBridge(str(tmp_path / "release" / "release.stl"), stl_bytes)
        release = make_manufacture_release(
            twin, blob_stager=stager, mcp_bridge=bridge, workspace_dir=tmp_path
        )

        out = await release(work_product_id=str(wp.id), process="3d_print")

        assert stager.calls == [str(wp.id)]
        assert bridge.calls[0][0] == "cadquery.export_geometry"
        assert bridge.calls[0][1]["input_file"] == stager.file_path
        assert bridge.calls[0][1]["output_format"] == "stl"
        assert out["process"] == "3d_print"
        assert out["format"] == "stl"
        assert out["filename"] == "release.stl"
        assert out["file_size_bytes"] == len(stl_bytes)
        assert base64.b64decode(out["content_base64"]) == stl_bytes

    async def test_cnc_process_exports_step(self, twin: InMemoryTwinAPI, tmp_path: Path) -> None:
        wp = await _seed_wp(twin)
        stager = _FakeBlobStager(str(tmp_path / "staged" / "upper_arm.step"))
        step_bytes = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
        bridge = _FakeBridge(str(tmp_path / "release" / "release.step"), step_bytes)
        release = make_manufacture_release(
            twin, blob_stager=stager, mcp_bridge=bridge, workspace_dir=tmp_path
        )

        out = await release(work_product_id=str(wp.id), process="cnc")

        assert bridge.calls[0][1]["output_format"] == "step"
        assert out["format"] == "step"
        assert base64.b64decode(out["content_base64"]) == step_bytes

    async def test_process_export_formats_map(self) -> None:
        assert PROCESS_EXPORT_FORMATS == {"3d_print": "stl", "cnc": "step"}

    async def test_does_not_pre_create_the_output_directory(
        self, twin: InMemoryTwinAPI, tmp_path: Path
    ) -> None:
        """Regression test for a real cross-container permission bug found
        during live validation on fidel-dev: the gateway container runs as
        root, but every adapter container runs as a non-root `metaforge`
        user. A directory the gateway pre-creates on the shared workspace
        volume is root-owned 755 -- the adapter can stat/traverse it but
        not write into it, and CadQuery's OCCT-backed exporter swallows
        that write failure silently (the file is just never created)
        rather than raising, surfacing only as a confusing downstream
        os.path.getsize() ENOENT with no obvious link to ownership.
        cadquery.export_geometry's own handler already mkdirs its output
        path itself before exporting -- this evaluator must NOT also
        create that directory from the gateway side, or it reintroduces
        the ownership mismatch."""
        wp = await _seed_wp(twin)
        stager = _FakeBlobStager(str(tmp_path / "staged" / "upper_arm.step"))
        # Matches make_manufacture_release's own release_dir computation
        # (workspace_dir / _RELEASE_SUBDIR / work_product_id) exactly.
        release_output_dir = tmp_path / "_manufacture_releases" / str(wp.id)

        class _LazyMkdirBridge:
            """Mirrors the real adapter's timing: the output directory is
            created only when the export tool actually runs, not upfront."""

            def __init__(self) -> None:
                self.invoked = False

            async def invoke(
                self, tool_id: str, params: dict[str, Any], timeout: int | None = None
            ) -> dict[str, Any]:
                self.invoked = True
                # The real assertion: this directory must not already
                # exist when the "adapter" is invoked -- if it did, the
                # gateway pre-created it (the bug).
                assert not release_output_dir.exists(), (
                    "manufacture_release pre-created the output directory -- "
                    "this reintroduces the cross-container permission bug"
                )
                out = Path(params["output_path"])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"solid demo\nendsolid demo\n")
                return {
                    "output_file": str(out),
                    "file_size_bytes": out.stat().st_size,
                    "format": "stl",
                }

        bridge = _LazyMkdirBridge()
        release = make_manufacture_release(
            twin, blob_stager=stager, mcp_bridge=bridge, workspace_dir=tmp_path
        )

        assert not release_output_dir.exists()
        out = await release(work_product_id=str(wp.id), process="3d_print")

        assert bridge.invoked is True
        assert out["file_size_bytes"] > 0
