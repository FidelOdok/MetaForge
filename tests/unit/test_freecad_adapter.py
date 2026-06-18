"""Tests for the FreeCAD MCP tool adapter."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.freecad.adapter import FreecadServer
from tool_registry.tools.freecad.config import FreecadConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> FreecadServer:
    """Bare FreeCAD server (no mocks on internal methods)."""
    return FreecadServer()


@pytest.fixture()
def server_with_mocks() -> FreecadServer:
    """Server with mocked internal methods for testing."""
    s = FreecadServer()
    s._execute_export = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_file": "/tmp/freecad/bracket.stl",
            "file_size_bytes": 245760,
            "format": "stl",
        }
    )
    s._execute_meshing = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "mesh_file": "/tmp/freecad/bracket.inp",
            "num_nodes": 12500,
            "num_elements": 48000,
            "element_types": ["C3D10", "C3D4"],
            "quality_metrics": {
                "min_angle": 18.5,
                "max_aspect_ratio": 4.2,
                "avg_quality": 0.87,
            },
        }
    )
    s._execute_boolean = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_file": "/tmp/freecad/body_union.step",
            "operation": "union",
            "result_volume": 1250.5,
            "result_area": 890.3,
        }
    )
    s._execute_analysis = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "file": "/models/bracket.step",
            "properties": {
                "volume": 1250.5,
                "surface_area": 890.3,
                "center_of_mass": {"x": 10.0, "y": 5.0, "z": 3.2},
                "bounding_box": {
                    "min_x": 0.0,
                    "min_y": 0.0,
                    "min_z": 0.0,
                    "max_x": 20.0,
                    "max_y": 10.0,
                    "max_z": 6.4,
                },
            },
        }
    )
    return s


# ---------------------------------------------------------------------------
# TestFreecadConfig
# ---------------------------------------------------------------------------


class TestFreecadConfig:
    def test_default_config(self) -> None:
        cfg = FreecadConfig()
        assert cfg.freecad_binary == "freecadcmd"
        assert cfg.work_dir == "/tmp/freecad"
        assert cfg.max_operation_time == 300
        assert cfg.max_memory_mb == 2048
        assert cfg.supported_import_formats == ["step", "stp", "stl", "iges", "igs", "brep"]
        assert cfg.supported_export_formats == ["step", "stp", "stl", "obj", "brep"]
        assert cfg.default_mesh_algorithm == "netgen"

    def test_custom_config(self) -> None:
        cfg = FreecadConfig(
            freecad_binary="/usr/local/bin/freecadcmd",
            work_dir="/data/freecad",
            max_operation_time=120,
            max_memory_mb=4096,
            supported_import_formats=["step", "stl"],
            supported_export_formats=["step"],
            default_mesh_algorithm="gmsh",
        )
        assert cfg.freecad_binary == "/usr/local/bin/freecadcmd"
        assert cfg.work_dir == "/data/freecad"
        assert cfg.max_operation_time == 120
        assert cfg.max_memory_mb == 4096
        assert cfg.supported_import_formats == ["step", "stl"]
        assert cfg.supported_export_formats == ["step"]
        assert cfg.default_mesh_algorithm == "gmsh"

    def test_supported_formats(self) -> None:
        cfg = FreecadConfig()
        # Import should include STEP, STL, IGES, BREP
        assert "step" in cfg.supported_import_formats
        assert "stl" in cfg.supported_import_formats
        assert "iges" in cfg.supported_import_formats
        assert "brep" in cfg.supported_import_formats
        # Export should include STEP, STL, OBJ, BREP
        assert "step" in cfg.supported_export_formats
        assert "stl" in cfg.supported_export_formats
        assert "obj" in cfg.supported_export_formats
        assert "brep" in cfg.supported_export_formats


# ---------------------------------------------------------------------------
# TestFreecadServer
# ---------------------------------------------------------------------------


class TestFreecadServer:
    def test_server_adapter_id(self, server: FreecadServer) -> None:
        assert server.adapter_id == "freecad"

    def test_server_version(self, server: FreecadServer) -> None:
        assert server.version == "0.2.0"

    def test_registers_all_tools(self, server: FreecadServer) -> None:
        # 5 stateless + 8 authoring + 8 features + 4 assembly + 2 inspect + 2 parametric + 1 script.
        assert len(server.tool_ids) == 33

    def test_tool_ids(self, server: FreecadServer) -> None:
        expected = {
            # stateless file-based
            "freecad.export_geometry",
            "freecad.generate_mesh",
            "freecad.boolean_operation",
            "freecad.get_properties",
            "freecad.create_parametric",
            # stateful authoring (MET-528)
            "freecad.open_session",
            "freecad.close_session",
            "freecad.describe_session",
            "freecad.create_primitive",
            "freecad.create_body",
            "freecad.create_sketch",
            "freecad.pad_sketch",
            "freecad.pocket_sketch",
            "freecad.revolve_sketch",
            "freecad.transform_object",
            "freecad.linear_pattern",
            "freecad.polar_pattern",
            "freecad.mirror_feature",
            "freecad.loft_sketches",
            "freecad.sweep_sketch",
            "freecad.execute_code",
            "freecad.fillet_edges",
            "freecad.chamfer_edges",
            "freecad.shell_solid",
            "freecad.export_model",
            # assembly authoring (MET-530)
            "freecad.create_assembly",
            "freecad.add_part_to_assembly",
            "freecad.add_assembly_joint",
            "freecad.list_joints",
            "freecad.measure",
            "freecad.describe_model",
            # parametric (MET-531)
            "freecad.create_variable_set",
            "freecad.set_expression",
        }
        assert set(server.tool_ids) == expected


# ---------------------------------------------------------------------------
# TestExportGeometry
# ---------------------------------------------------------------------------


class TestExportGeometry:
    async def test_export_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.export_geometry(
            {
                "input_file": "/models/bracket.step",
                "output_format": "stl",
                "output_path": "/tmp/freecad/bracket.stl",
            }
        )
        assert result["output_file"] == "/tmp/freecad/bracket.stl"
        assert result["file_size_bytes"] == 245760
        assert result["format"] == "stl"

    async def test_export_missing_input_file_raises(self, server_with_mocks: FreecadServer) -> None:
        with pytest.raises(ValueError, match="input_file is required"):
            await server_with_mocks.export_geometry({"input_file": "", "output_format": "stl"})

    async def test_export_unsupported_format_raises(self, server_with_mocks: FreecadServer) -> None:
        with pytest.raises(ValueError, match="Unsupported export format"):
            await server_with_mocks.export_geometry(
                {"input_file": "/models/bracket.step", "output_format": "fbx"}
            )


# ---------------------------------------------------------------------------
# TestGenerateMesh
# ---------------------------------------------------------------------------


class TestGenerateMesh:
    async def test_generate_mesh_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.generate_mesh(
            {
                "input_file": "/models/bracket.step",
                "element_size": 0.5,
                "algorithm": "netgen",
                "output_format": "inp",
            }
        )
        assert result["mesh_file"] == "/tmp/freecad/bracket.inp"
        assert result["num_nodes"] == 12500
        assert result["num_elements"] == 48000
        assert result["element_types"] == ["C3D10", "C3D4"]
        assert result["quality_metrics"]["min_angle"] == 18.5
        assert result["quality_metrics"]["max_aspect_ratio"] == 4.2
        assert result["quality_metrics"]["avg_quality"] == 0.87

    async def test_generate_mesh_default_params(self, server_with_mocks: FreecadServer) -> None:
        """generate_mesh uses default element_size, algorithm, and output_format."""
        result = await server_with_mocks.generate_mesh({"input_file": "/models/bracket.step"})
        assert result["num_nodes"] == 12500
        # Verify _execute_meshing was called with default values
        call_args = server_with_mocks._execute_meshing.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == 1.0  # default element_size
        assert call_args[0][2] == "netgen"  # default algorithm
        assert call_args[0][3] == "inp"  # default output_format

    async def test_generate_mesh_missing_input_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="input_file is required"):
            await server_with_mocks.generate_mesh({"input_file": ""})

    async def test_generate_mesh_unsupported_algorithm_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported meshing algorithm"):
            await server_with_mocks.generate_mesh(
                {"input_file": "/models/bracket.step", "algorithm": "tetgen"}
            )


# ---------------------------------------------------------------------------
# TestBooleanOperation
# ---------------------------------------------------------------------------


class TestBooleanOperation:
    async def test_boolean_union_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.boolean_operation(
            {
                "input_file_a": "/models/body.step",
                "input_file_b": "/models/flange.step",
                "operation": "union",
            }
        )
        assert result["output_file"] == "/tmp/freecad/body_union.step"
        assert result["operation"] == "union"
        assert result["result_volume"] == 1250.5
        assert result["result_area"] == 890.3

    async def test_boolean_subtract_success(self, server_with_mocks: FreecadServer) -> None:
        # Reconfigure mock for subtract
        server_with_mocks._execute_boolean = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "output_file": "/tmp/freecad/body_subtract.step",
                "operation": "subtract",
                "result_volume": 800.0,
                "result_area": 650.2,
            }
        )
        result = await server_with_mocks.boolean_operation(
            {
                "input_file_a": "/models/body.step",
                "input_file_b": "/models/hole.step",
                "operation": "subtract",
            }
        )
        assert result["operation"] == "subtract"
        assert result["result_volume"] == 800.0

    async def test_boolean_missing_files_raises(self, server_with_mocks: FreecadServer) -> None:
        with pytest.raises(ValueError, match="input_file_a is required"):
            await server_with_mocks.boolean_operation(
                {
                    "input_file_a": "",
                    "input_file_b": "/models/flange.step",
                    "operation": "union",
                }
            )
        with pytest.raises(ValueError, match="input_file_b is required"):
            await server_with_mocks.boolean_operation(
                {
                    "input_file_a": "/models/body.step",
                    "input_file_b": "",
                    "operation": "union",
                }
            )

    async def test_boolean_invalid_operation_raises(self, server_with_mocks: FreecadServer) -> None:
        with pytest.raises(ValueError, match="Unsupported boolean operation"):
            await server_with_mocks.boolean_operation(
                {
                    "input_file_a": "/models/body.step",
                    "input_file_b": "/models/flange.step",
                    "operation": "xor",
                }
            )


# ---------------------------------------------------------------------------
# TestGetProperties
# ---------------------------------------------------------------------------


class TestGetProperties:
    async def test_get_properties_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.get_properties(
            {
                "input_file": "/models/bracket.step",
                "properties": ["volume", "area", "center_of_mass", "bounding_box"],
            }
        )
        assert result["file"] == "/models/bracket.step"
        assert result["properties"]["volume"] == 1250.5
        assert result["properties"]["surface_area"] == 890.3
        assert result["properties"]["center_of_mass"]["x"] == 10.0
        assert result["properties"]["bounding_box"]["max_x"] == 20.0

    async def test_get_properties_default_fields(self, server_with_mocks: FreecadServer) -> None:
        """get_properties uses default property list when not specified."""
        result = await server_with_mocks.get_properties({"input_file": "/models/bracket.step"})
        assert result["file"] == "/models/bracket.step"
        # Verify _execute_analysis was called with default properties
        call_args = server_with_mocks._execute_analysis.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == ["volume", "area", "center_of_mass", "bounding_box"]

    async def test_get_properties_missing_file_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="input_file is required"):
            await server_with_mocks.get_properties({"input_file": ""})


# ---------------------------------------------------------------------------
# TestUnmockedMethodsRaise
# ---------------------------------------------------------------------------


class TestUnmockedMethodsDegrade:
    """Without FreeCAD bindings, the internal methods delegate to FreecadOperations
    which raises FreecadNotAvailableError (graceful degradation). On a host that
    *does* have FreeCAD these would execute, so the class is skipped there."""

    pytestmark = pytest.mark.skipif(
        __import__("tool_registry.tools.freecad.operations", fromlist=["HAS_FREECAD"]).HAS_FREECAD,
        reason="FreeCAD installed; degradation path not exercised",
    )

    async def test_export_degrades(self, server: FreecadServer) -> None:
        from tool_registry.tools.freecad.operations import FreecadNotAvailableError

        with pytest.raises(FreecadNotAvailableError):
            await server._execute_export("/models/test.step", "step", "/tmp/out.step")

    async def test_mesh_degrades(self, server: FreecadServer) -> None:
        from tool_registry.tools.freecad.operations import FreecadNotAvailableError

        with pytest.raises(FreecadNotAvailableError):
            await server._execute_meshing("/models/test.step", 1.0, "netgen", "inp")

    async def test_boolean_degrades(self, server: FreecadServer) -> None:
        from tool_registry.tools.freecad.operations import FreecadNotAvailableError

        with pytest.raises(FreecadNotAvailableError):
            await server._execute_boolean(
                "/models/a.step", "/models/b.step", "union", "/tmp/out.step"
            )

    async def test_analysis_degrades(self, server: FreecadServer) -> None:
        from tool_registry.tools.freecad.operations import FreecadNotAvailableError

        with pytest.raises(FreecadNotAvailableError):
            await server._execute_analysis("/models/test.step", ["volume"])


# ---------------------------------------------------------------------------
# TestJsonRpcIntegration
# ---------------------------------------------------------------------------


class _FakeObj:
    """Stand-in for a live FreeCAD object."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


class TestStatefulAuthoring:
    """Session + PartDesign orchestration with FreeCAD geometry mocked out, so
    the wiring (session store ↔ operations ↔ obj_id registry) is verified without
    FreeCAD bindings."""

    @pytest.fixture()
    def authoring_server(self) -> FreecadServer:
        from unittest.mock import MagicMock

        from tool_registry.tools.freecad.session import FreecadSessionStore

        s = FreecadServer()
        # Fake, FreeCAD-free document lifecycle.
        s._sessions = FreecadSessionStore(
            doc_factory=lambda name: MagicMock(name=f"doc:{name}"),
            doc_closer=lambda doc: None,
        )
        # Mock the geometry layer; each builder returns a tagged fake object.
        ops = MagicMock()
        ops.create_primitive.return_value = _FakeObj("box")
        ops.create_body.return_value = _FakeObj("body")
        ops.create_sketch.return_value = _FakeObj("sketch")
        ops.pad_sketch.return_value = _FakeObj("pad")
        ops.shape_props.return_value = {
            "volume_mm3": 1000.0,
            "surface_area_mm2": 600.0,
            "bounding_box": {"min_x": 0, "max_x": 10},
        }
        ops.export_object_step_bytes.return_value = b"ISO-10303-21;\nfake-step\n"
        ops.create_assembly.return_value = _FakeObj("assembly")
        ops.add_part_to_assembly.return_value = _FakeObj("part")
        ops.pocket_sketch.return_value = _FakeObj("pocket")
        ops.revolve_sketch.return_value = _FakeObj("revolution")
        ops.transform_object.return_value = _FakeObj("moved")
        ops.fillet_edges.return_value = _FakeObj("fillet")
        ops.chamfer_edges.return_value = _FakeObj("chamfer")
        ops.shell_solid.return_value = _FakeObj("shell")
        ops.linear_pattern.return_value = _FakeObj("lpat")
        ops.polar_pattern.return_value = _FakeObj("ppat")
        ops.mirror_feature.return_value = _FakeObj("mirror")
        ops.measure.return_value = {"volume_mm3": 1000.0, "edge_count": 12, "face_count": 6}
        ops.describe_model.return_value = {"dimensions_mm": {"x": 10, "y": 10, "z": 10}}
        ops.execute_code.return_value = None  # script touched the doc, surfaced nothing
        s._ops = ops  # type: ignore[assignment]
        return s

    async def test_full_authoring_flow(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({"name": "widget"}))["session_id"]
        assert sid

        body = await s.create_body({"session_id": sid, "name": "Main"})
        assert body["obj_id"] == "body_1"

        sketch = await s.create_sketch(
            {
                "session_id": sid,
                "body_id": body["obj_id"],
                "plane": "XY",
                "elements": [{"type": "rectangle", "x": 0, "y": 0, "width": 20, "height": 10}],
            }
        )
        assert sketch["obj_id"] == "sketch_2"
        assert sketch["body_id"] == "body_1"

        pad = await s.pad_sketch(
            {
                "session_id": sid,
                "body_id": body["obj_id"],
                "sketch_id": sketch["obj_id"],
                "length": 5,
            }
        )
        assert pad["obj_id"] == "feature_3"
        assert pad["volume_mm3"] == 1000.0

        # describe_session lists the three objects in creation order.
        desc = await s.describe_session({"session_id": sid})
        assert [o["obj_id"] for o in desc["objects"]] == ["body_1", "sketch_2", "feature_3"]

    async def test_export_model_returns_base64_step(self, authoring_server: FreecadServer) -> None:
        import base64

        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        prim = await s.create_primitive({"session_id": sid, "kind": "box", "parameters": {}})
        result = await s.export_model({"session_id": sid, "obj_id": prim["obj_id"]})
        assert result["format"] == "step"
        assert result["size_bytes"] == len(b"ISO-10303-21;\nfake-step\n")
        assert base64.b64decode(result["step_base64"]) == b"ISO-10303-21;\nfake-step\n"
        assert result["volume_mm3"] == 1000.0

    async def test_create_primitive_passes_document_and_kind(
        self, authoring_server: FreecadServer
    ) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        await s.create_primitive(
            {"session_id": sid, "kind": "cylinder", "parameters": {"radius": 4}}
        )
        # The live session document and kind/params reach the geometry layer.
        call = s._ops.create_primitive.call_args  # type: ignore[attr-defined]
        assert call[0][1] == "cylinder"
        assert call[0][2] == {"radius": 4}

    async def test_unknown_session_raises(self, authoring_server: FreecadServer) -> None:
        from tool_registry.tools.freecad.session import SessionNotFoundError

        with pytest.raises(SessionNotFoundError):
            await authoring_server.describe_session({"session_id": "ghost"})

    async def test_missing_required_arg_raises(self, authoring_server: FreecadServer) -> None:
        with pytest.raises(ValueError, match="session_id is required"):
            await authoring_server.describe_session({})

    async def test_close_session(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        assert (await s.close_session({"session_id": sid}))["closed"] is True
        assert (await s.close_session({"session_id": sid}))["closed"] is False

    async def test_pocket_and_revolve_register_features(
        self, authoring_server: FreecadServer
    ) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        sketch = await s.create_sketch({"session_id": sid, "body_id": body["obj_id"]})
        pocket = await s.pocket_sketch(
            {
                "session_id": sid,
                "body_id": body["obj_id"],
                "sketch_id": sketch["obj_id"],
                "depth": 4,
            }
        )
        rev = await s.revolve_sketch(
            {
                "session_id": sid,
                "body_id": body["obj_id"],
                "sketch_id": sketch["obj_id"],
                "angle": 270,
                "axis": "H",
            }
        )
        assert pocket["kind"] == "feature" and rev["kind"] == "feature"
        # depth + angle/axis reach the geometry layer.
        assert s._ops.pocket_sketch.call_args[0][3] == 4.0  # type: ignore[attr-defined]
        assert s._ops.revolve_sketch.call_args[0][3] == 270.0  # type: ignore[attr-defined]
        assert s._ops.revolve_sketch.call_args[1]["axis"] == "H"  # type: ignore[attr-defined]

    async def test_pocket_requires_depth(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        sketch = await s.create_sketch({"session_id": sid, "body_id": body["obj_id"]})
        with pytest.raises(ValueError, match="depth is required"):
            await s.pocket_sketch(
                {"session_id": sid, "body_id": body["obj_id"], "sketch_id": sketch["obj_id"]}
            )

    async def test_fillet_and_chamfer_register_features(
        self, authoring_server: FreecadServer
    ) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        fil = await s.fillet_edges({"session_id": sid, "body_id": body["obj_id"], "radius": 2})
        cha = await s.chamfer_edges({"session_id": sid, "body_id": body["obj_id"], "size": 1})
        assert fil["kind"] == cha["kind"] == "feature"
        # amounts + (defaulted) selectors reach the geometry layer.
        assert s._ops.fillet_edges.call_args[0][2] == 2.0  # type: ignore[attr-defined]
        assert s._ops.fillet_edges.call_args[0][3] is None  # default all edges
        assert s._ops.chamfer_edges.call_args[0][2] == 1.0  # type: ignore[attr-defined]

    async def test_patterns_and_mirror_register_features(
        self, authoring_server: FreecadServer
    ) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        feat = await s.create_primitive({"session_id": sid, "kind": "box"})
        lp = await s.linear_pattern(
            {
                "session_id": sid,
                "body_id": body["obj_id"],
                "feature_id": feat["obj_id"],
                "count": 3,
                "spacing": 10,
                "axis": "Y",
            }
        )
        pp = await s.polar_pattern(
            {
                "session_id": sid,
                "body_id": body["obj_id"],
                "feature_id": feat["obj_id"],
                "count": 6,
            }
        )
        mr = await s.mirror_feature(
            {"session_id": sid, "body_id": body["obj_id"], "feature_id": feat["obj_id"]}
        )
        assert lp["kind"] == pp["kind"] == mr["kind"] == "feature"
        assert s._ops.linear_pattern.call_args[0][3] == 3  # count  # type: ignore[attr-defined]
        assert s._ops.linear_pattern.call_args[1]["axis"] == "Y"  # type: ignore[attr-defined]
        assert s._ops.polar_pattern.call_args[0][3] == 6  # type: ignore[attr-defined]

    async def test_linear_pattern_requires_count_spacing(
        self, authoring_server: FreecadServer
    ) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        feat = await s.create_primitive({"session_id": sid, "kind": "box"})
        with pytest.raises(ValueError, match="count and spacing"):
            await s.linear_pattern(
                {
                    "session_id": sid,
                    "body_id": body["obj_id"],
                    "feature_id": feat["obj_id"],
                    "count": 3,
                }
            )

    async def test_execute_code_forwards_and_reports(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        out = await s.execute_code({"session_id": sid, "code": "result = doc"})
        assert out["executed"] is True
        assert "session" in out  # no shape surfaced → session summary
        # The session document + code reach the geometry layer.
        assert s._ops.execute_code.call_args[0][1] == "result = doc"  # type: ignore[attr-defined]

    async def test_measure_and_describe(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        prim = await s.create_primitive({"session_id": sid, "kind": "box"})
        m = await s.measure({"session_id": sid, "obj_id": prim["obj_id"]})
        assert m["edge_count"] == 12 and m["face_count"] == 6
        d = await s.describe_model({"session_id": sid, "obj_id": prim["obj_id"]})
        assert d["dimensions_mm"]["x"] == 10
        # both resolved the session object and forwarded to the geometry layer
        assert s._ops.measure.called and s._ops.describe_model.called  # type: ignore[attr-defined]

    async def test_shell_solid(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        out = await s.shell_solid({"session_id": sid, "body_id": body["obj_id"], "thickness": 1.5})
        assert out["kind"] == "feature"
        # shell reports its own (Part::Feature) props, not the hidden body's
        assert s._ops.shell_solid.call_args[0][2] == 1.5  # type: ignore[attr-defined]

    async def test_shell_requires_thickness(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        with pytest.raises(ValueError, match="thickness is required"):
            await s.shell_solid({"session_id": sid, "body_id": body["obj_id"]})

    async def test_fillet_requires_radius(self, authoring_server: FreecadServer) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        body = await s.create_body({"session_id": sid})
        with pytest.raises(ValueError, match="radius is required"):
            await s.fillet_edges({"session_id": sid, "body_id": body["obj_id"]})

    async def test_transform_object_forwards_placement(
        self, authoring_server: FreecadServer
    ) -> None:
        s = authoring_server
        sid = (await s.open_session({}))["session_id"]
        prim = await s.create_primitive({"session_id": sid, "kind": "box"})
        out = await s.transform_object(
            {
                "session_id": sid,
                "obj_id": prim["obj_id"],
                "position": [10, 0, 5],
                "rotation": {"axis": [0, 0, 1], "angle_deg": 90},
            }
        )
        assert out["transformed"] is True
        call = s._ops.transform_object.call_args  # type: ignore[attr-defined]
        assert call[0][2] == [10, 0, 5]  # position
        assert call[0][3] == {"axis": [0, 0, 1], "angle_deg": 90}  # rotation


class TestAssemblyAuthoring:
    """Assembly + joint authoring (MET-530) — verifies joint metadata is recorded
    in the shape the live solver consumes, with FreeCAD geometry mocked out."""

    @pytest.fixture()
    def server(self) -> FreecadServer:
        from unittest.mock import MagicMock

        from tool_registry.tools.freecad.session import FreecadSessionStore

        s = FreecadServer()
        s._sessions = FreecadSessionStore(
            doc_factory=lambda name: MagicMock(name=f"doc:{name}"),
            doc_closer=lambda doc: None,
        )
        ops = MagicMock()
        ops.create_primitive.return_value = _FakeObj("prim")
        ops.create_assembly.return_value = _FakeObj("assembly")
        ops.add_part_to_assembly.return_value = _FakeObj("part")
        ops.shape_props.return_value = {"volume_mm3": 1.0}
        s._ops = ops  # type: ignore[assignment]
        return s

    async def _two_parts(self, s: FreecadServer, sid: str) -> tuple[str, str, str]:
        asm = (await s.create_assembly({"session_id": sid, "name": "robot"}))["obj_id"]
        base = (await s.create_primitive({"session_id": sid, "kind": "box"}))["obj_id"]
        arm = (await s.create_primitive({"session_id": sid, "kind": "cylinder"}))["obj_id"]
        await s.add_part_to_assembly(
            {"session_id": sid, "assembly_id": asm, "part_id": base, "position": [0, 0, 0]}
        )
        await s.add_part_to_assembly({"session_id": sid, "assembly_id": asm, "part_id": arm})
        return asm, base, arm

    async def test_add_joint_records_solver_shape(self, server: FreecadServer) -> None:
        s = server
        sid = (await s.open_session({}))["session_id"]
        asm, base, arm = await self._two_parts(s, sid)

        out = await s.add_assembly_joint(
            {
                "session_id": sid,
                "assembly_id": asm,
                "base_id": base,
                "follower_id": arm,
                "type": "Revolute",
                "axis": [0, 0, 1],
                "anchor": [5, 0, 0],
            }
        )
        joint = out["joint"]
        assert joint["type"] == "revolute"  # normalised
        # base/follower resolved to the part names (the viewer's group names).
        assert joint["base"] == "box"
        assert joint["follower"] == "cylinder"
        assert joint["axis"] == [0.0, 0.0, 1.0]
        assert joint["anchor"] == [5.0, 0.0, 0.0]

    async def test_list_joints_round_trips_through_kinematics(self, server: FreecadServer) -> None:
        # The keystone: what add_assembly_joint emits must be consumable by the
        # merged live solver (api_gateway.constraint.kinematics).
        from api_gateway.constraint.kinematics import Joint, solve_joint

        s = server
        sid = (await s.open_session({}))["session_id"]
        asm, base, arm = await self._two_parts(s, sid)
        await s.add_assembly_joint(
            {
                "session_id": sid,
                "assembly_id": asm,
                "base_id": base,
                "follower_id": arm,
                "type": "slider",
                "axis": [1, 0, 0],
            }
        )
        listed = await s.list_joints({"session_id": sid})
        assert len(listed["joints"]) == 1
        # Feed it straight into the solver — no translation needed.
        joint = Joint.from_dict(listed["joints"][0])
        sol = solve_joint(joint, (10.0, 7.0, 0.0))
        assert sol.delta == pytest.approx((10.0, 0.0, 0.0))  # slider clamps to X

    async def test_unknown_joint_type_raises(self, server: FreecadServer) -> None:
        s = server
        sid = (await s.open_session({}))["session_id"]
        asm, base, arm = await self._two_parts(s, sid)
        with pytest.raises(ValueError, match="unknown joint type"):
            await s.add_assembly_joint(
                {
                    "session_id": sid,
                    "assembly_id": asm,
                    "base_id": base,
                    "follower_id": arm,
                    "type": "weld",
                }
            )

    async def test_describe_session_surfaces_joint_metadata(self, server: FreecadServer) -> None:
        s = server
        sid = (await s.open_session({}))["session_id"]
        asm, base, arm = await self._two_parts(s, sid)
        await s.add_assembly_joint(
            {
                "session_id": sid,
                "assembly_id": asm,
                "base_id": base,
                "follower_id": arm,
                "type": "fixed",
            }
        )
        desc = await s.describe_session({"session_id": sid})
        joint_obj = next(o for o in desc["objects"] if o["kind"] == "joint")
        assert joint_obj["metadata"]["type"] == "fixed"


class TestParametric:
    """VarSet + set_expression (MET-531) with FreeCAD geometry mocked out."""

    @pytest.fixture()
    def server(self) -> FreecadServer:
        from unittest.mock import MagicMock

        from tool_registry.tools.freecad.session import FreecadSessionStore

        s = FreecadServer()
        s._sessions = FreecadSessionStore(
            doc_factory=lambda name: MagicMock(name=f"doc:{name}"),
            doc_closer=lambda doc: None,
        )
        ops = MagicMock()
        ops.create_primitive.return_value = _FakeObj("box")
        ops.create_variable_set.return_value = _FakeObj("varset")
        s._ops = ops  # type: ignore[assignment]
        return s

    async def test_create_variable_set_registers_and_forwards(self, server: FreecadServer) -> None:
        s = server
        sid = (await s.open_session({}))["session_id"]
        out = await s.create_variable_set(
            {
                "session_id": sid,
                "name": "Params",
                "variables": {"width": {"value": 40, "type": "length"}, "n": 3},
            }
        )
        assert out["kind"] == "varset"
        assert set(out["variables"]) == {"width", "n"}
        # Variables reached the geometry layer.
        call = s._ops.create_variable_set.call_args  # type: ignore[attr-defined]
        assert call[0][1] == "Params"
        assert "width" in call[0][2]

    async def test_create_variable_set_requires_variables(self, server: FreecadServer) -> None:
        sid = (await server.open_session({}))["session_id"]
        with pytest.raises(ValueError, match="variables is required"):
            await server.create_variable_set({"session_id": sid, "variables": {}})

    async def test_set_expression_binds_property(self, server: FreecadServer) -> None:
        s = server
        sid = (await s.open_session({}))["session_id"]
        prim = await s.create_primitive({"session_id": sid, "kind": "box"})
        out = await s.set_expression(
            {
                "session_id": sid,
                "obj_id": prim["obj_id"],
                "property": "Length",
                "expression": "Params.width * 2",
            }
        )
        assert out["property"] == "Length"
        assert out["expression"] == "Params.width * 2"
        call = s._ops.set_expression.call_args  # type: ignore[attr-defined]
        assert call[0][2] == "Length"
        assert call[0][3] == "Params.width * 2"

    async def test_set_expression_requires_fields(self, server: FreecadServer) -> None:
        sid = (await server.open_session({}))["session_id"]
        prim = await server.create_primitive({"session_id": sid, "kind": "box"})
        with pytest.raises(ValueError, match="property is required"):
            await server.set_expression(
                {"session_id": sid, "obj_id": prim["obj_id"], "expression": "x"}
            )


def _make_jsonrpc(
    method: str,
    params: dict[str, Any] | None = None,
    request_id: str = "1",
) -> str:
    """Helper to build a JSON-RPC 2.0 request string."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    return json.dumps(msg)


class TestJsonRpcIntegration:
    async def test_tool_list_returns_all_tools(self, server: FreecadServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert len(response["result"]["tools"]) == 33

    async def test_tool_call_export(self, server_with_mocks: FreecadServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "freecad.export_geometry",
                "arguments": {
                    "input_file": "/models/bracket.step",
                    "output_format": "stl",
                    "output_path": "/tmp/freecad/bracket.stl",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert response["result"]["status"] == "success"
        assert response["result"]["tool_id"] == "freecad.export_geometry"
        data = response["result"]["data"]
        assert data["output_file"] == "/tmp/freecad/bracket.stl"
        assert "duration_ms" in response["result"]

    async def test_tool_call_mesh(self, server_with_mocks: FreecadServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "freecad.generate_mesh",
                "arguments": {
                    "input_file": "/models/bracket.step",
                    "element_size": 0.5,
                    "algorithm": "netgen",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["num_nodes"] == 12500

    async def test_health_check(self, server: FreecadServer) -> None:
        request = _make_jsonrpc("health/check")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["adapter_id"] == "freecad"
        assert response["result"]["status"] == "healthy"
        assert response["result"]["version"] == "0.2.0"
        assert response["result"]["tools_available"] == 33

    async def test_tool_list_filter_by_capability(self, server: FreecadServer) -> None:
        request = _make_jsonrpc("tool/list", {"capability": "cad_export"})
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        tools = response["result"]["tools"]
        # cad_export now covers the stateless export_geometry + stateful export_model.
        assert {t["tool_id"] for t in tools} == {
            "freecad.export_geometry",
            "freecad.export_model",
        }
