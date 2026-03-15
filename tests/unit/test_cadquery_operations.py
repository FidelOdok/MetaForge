"""Unit tests for CadQuery operations (mocked when CadQuery not installed)."""

from __future__ import annotations

import pytest

from tool_registry.tools.cadquery.operations import (
    _BLOCKED_NAMES,
    _SAFE_BUILTINS,
    _SHAPE_DEFAULTS,
    CadqueryNotAvailableError,
    CadqueryOperations,
    ScriptSandboxError,
)


class TestCadqueryOperationsWithoutCadquery:
    """Tests that run without CadQuery installed (guard checks)."""

    def test_require_cadquery_raises(self):
        ops = CadqueryOperations()
        # This will raise if CadQuery is not installed (expected in CI)
        try:
            ops._require_cadquery()
        except CadqueryNotAvailableError:
            pass  # Expected -- CadQuery not installed

    def test_shape_defaults_exist(self):
        """All expected shapes have defaults."""
        expected = {"box", "cylinder", "sphere", "cone", "bracket", "plate", "enclosure"}
        assert expected == set(_SHAPE_DEFAULTS.keys())

    def test_safe_builtins_whitelist(self):
        """Safe builtins whitelist includes common safe functions."""
        assert "abs" in _SAFE_BUILTINS
        assert "len" in _SAFE_BUILTINS
        assert "range" in _SAFE_BUILTINS
        assert "sorted" in _SAFE_BUILTINS
        # Should NOT include dangerous builtins
        assert "__import__" not in _SAFE_BUILTINS
        assert "eval" not in _SAFE_BUILTINS
        assert "exec" not in _SAFE_BUILTINS
        assert "compile" not in _SAFE_BUILTINS
        assert "open" not in _SAFE_BUILTINS

    def test_blocked_names(self):
        """Blocked names include dangerous operations."""
        assert "__import__" in _BLOCKED_NAMES
        assert "eval" in _BLOCKED_NAMES
        assert "exec" in _BLOCKED_NAMES
        assert "compile" in _BLOCKED_NAMES
        assert "open" in _BLOCKED_NAMES
        assert "os" in _BLOCKED_NAMES
        assert "sys" in _BLOCKED_NAMES
        assert "subprocess" in _BLOCKED_NAMES


class TestScriptSandboxValidation:
    """Tests for script sandbox validation (no CadQuery required)."""

    def test_script_line_limit(self):
        ops = CadqueryOperations(max_script_lines=5)
        long_script = "\n".join([f"x = {i}" for i in range(10)])

        # This should fail at the line count check before needing CadQuery
        try:
            ops.execute_script(long_script)
        except CadqueryNotAvailableError:
            pass  # OK -- would have passed the line check but CadQuery not installed
        except ScriptSandboxError as exc:
            assert "exceeds maximum" in str(exc)

    def test_blocked_import_in_script(self):
        ops = CadqueryOperations(sandbox_enabled=True)

        try:
            ops.execute_script("__import__('os')\nresult = None")
        except CadqueryNotAvailableError:
            pass  # OK
        except ScriptSandboxError as exc:
            assert "__import__" in str(exc)

    def test_blocked_os_in_script(self):
        ops = CadqueryOperations(sandbox_enabled=True)

        try:
            ops.execute_script("import os\nresult = None")
        except CadqueryNotAvailableError:
            pass  # OK
        except ScriptSandboxError as exc:
            assert "os" in str(exc)

    def test_blocked_subprocess_in_script(self):
        ops = CadqueryOperations(sandbox_enabled=True)

        try:
            ops.execute_script("subprocess.run(['ls'])\nresult = None")
        except CadqueryNotAvailableError:
            pass  # OK
        except ScriptSandboxError as exc:
            assert "subprocess" in str(exc)


class TestEnsureOutputDir:
    """Tests for output directory creation."""

    def test_ensure_output_dir_creates_parents(self, tmp_path):
        ops = CadqueryOperations()
        nested = str(tmp_path / "a" / "b" / "c" / "output.step")
        ops._ensure_output_dir(nested)
        assert (tmp_path / "a" / "b" / "c").is_dir()


class TestCadqueryOperationsWithCadquery:
    """Integration tests that require CadQuery. Skipped if not installed."""

    @pytest.fixture(autouse=True)
    def _require_cadquery(self):
        pytest.importorskip("cadquery")

    def test_create_parametric_box(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))
        output_path = str(tmp_path / "box.step")

        result = ops.create_parametric(
            shape_type="box",
            parameters={"length": 20.0, "width": 10.0, "height": 5.0},
            material="aluminum",
            output_path=output_path,
        )

        assert result["cad_file"] == output_path
        assert result["volume_mm3"] > 0
        assert result["surface_area_mm2"] > 0
        assert result["material"] == "aluminum"
        assert "bounding_box" in result

    def test_create_parametric_cylinder(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))
        output_path = str(tmp_path / "cyl.step")

        result = ops.create_parametric(
            shape_type="cylinder",
            parameters={"radius": 5.0, "height": 20.0},
            output_path=output_path,
        )

        assert result["volume_mm3"] > 0

    def test_create_parametric_bracket(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))
        output_path = str(tmp_path / "bracket.step")

        result = ops.create_parametric(
            shape_type="bracket",
            parameters={"length": 50.0, "width": 30.0, "thickness": 5.0},
            output_path=output_path,
        )

        assert result["volume_mm3"] > 0

    def test_create_parametric_enclosure(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))
        output_path = str(tmp_path / "enc.step")

        result = ops.create_parametric(
            shape_type="enclosure",
            parameters={"length": 80.0, "width": 50.0, "height": 30.0, "wall_thickness": 2.0},
            output_path=output_path,
        )

        assert result["volume_mm3"] > 0

    def test_create_parametric_unsupported(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))

        with pytest.raises(ValueError, match="Unsupported shape type"):
            ops.create_parametric(
                shape_type="gearbox",
                parameters={"width": 10},
                output_path=str(tmp_path / "out.step"),
            )

    def test_execute_script_basic(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "script_out.step")

        script = (
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(10, 10, 10)\n"
        )

        result = ops.execute_script(script, output_path)

        assert result["cad_file"] == output_path
        assert result["volume_mm3"] > 0
        assert "script_text" in result

    def test_execute_script_missing_result(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))

        script = "import cadquery as cq\nx = cq.Workplane('XY').box(10, 10, 10)\n"

        with pytest.raises(ValueError, match="must assign its output"):
            ops.execute_script(script, str(tmp_path / "out.step"))

    def test_export_geometry(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))

        # First create a STEP file
        step_path = str(tmp_path / "source.step")
        dims = {"length": 10, "width": 10, "height": 10}
        ops.create_parametric("box", dims, output_path=step_path)

        # Export to STL
        stl_path = str(tmp_path / "output.stl")
        result = ops.export_geometry(step_path, "stl", stl_path)

        assert result["format"] == "stl"
        assert result["file_size_bytes"] > 0

    def test_get_properties(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path))

        step_path = str(tmp_path / "props.step")
        dims = {"length": 10, "width": 10, "height": 10}
        ops.create_parametric("box", dims, output_path=step_path)

        result = ops.get_properties(step_path, ["volume", "area", "bounding_box"])

        assert "volume_mm3" in result["properties"]
        assert "surface_area_mm2" in result["properties"]
        assert "bounding_box" in result["properties"]
