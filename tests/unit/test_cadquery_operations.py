"""Unit tests for CadQuery operations (mocked when CadQuery not installed)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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

        script = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"

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


class _FakeBoundBox:
    xmin = ymin = zmin = 0.0
    xmax = ymax = zmax = 10.0


class _FakeSolid:
    def BoundingBox(self):  # noqa: N802
        return _FakeBoundBox()

    def Volume(self):  # noqa: N802
        return 1000.0

    def Area(self):  # noqa: N802
        return 600.0


class _FakeExporters:
    @staticmethod
    def export(_obj, output_path):
        with open(output_path, "wb") as f:  # noqa: PTH123
            f.write(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")


class _FakeCq:
    exporters = _FakeExporters()
    Vector = object
    # Mirrors real cadquery: `dir(cadquery)` includes the package's own
    # internal `cq` submodule under that exact name (`cadquery.cq`) -- a
    # collision the MET-702 pre-binding fix must not fall into (see
    # test_bare_cq_name_is_not_shadowed_by_the_internal_cq_submodule).
    cq = "the internal cadquery.cq submodule, NOT the top-level package"

    @staticmethod
    def Workplane(*_args, **_kwargs):  # noqa: N802
        return _FakeSolid()


class TestExecuteScriptStepBase64:
    """MET-648: CadQuery output had no path into twin.commit_geometry --
    unlike freecad.export_model, execute_script never returned base64 STEP
    bytes, so the model would generate correct geometry via CadQuery and
    then have nothing valid to hand to commit_geometry's step_base64 arg."""

    def test_step_output_includes_step_base64(self, tmp_path):
        import base64

        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "out.step")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script("result = cq.Workplane()", output_path)
        assert "step_base64" in result
        decoded = base64.b64decode(result["step_base64"])
        assert decoded == Path(output_path).read_bytes()

    def test_non_step_output_omits_step_base64(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "out.stl")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script("result = cq.Workplane()", output_path)
        assert "step_base64" not in result


class TestExecuteScriptSandboxedImport:
    """MET-645 follow-up, duplicated here: the identical bug exists in the
    CadQuery adapter (same _strip_sandbox_imports pattern, same missing
    __import__). A dotted submodule import or comma-separated import list
    bypasses the regex and previously crashed with "__import__ not found"."""

    def test_dotted_submodule_import_no_longer_crashes(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script(
                "import cadquery.occ_impl\nresult = cq.Workplane()",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")

    def test_import_of_disallowed_module_raises(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            with pytest.raises(RuntimeError, match="import of 'json' is not permitted"):
                ops.execute_script("import json\nresult = json.dumps({})", str(tmp_path / "o.step"))


class TestExecuteScriptExceptionTypes:
    """Ported from the FreeCAD fix (found live during the MET-642 S3 eval):
    exception types were entirely missing from _SAFE_BUILTINS here too."""

    def test_try_except_exception_no_longer_crashes(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script(
                "try:\n    raise ValueError('x')\nexcept Exception:\n    result = cq.Workplane()\n",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")


class TestExecuteScriptSandboxConvenienceNames:
    """MET-649: `from math import sin, cos` is stripped by
    _strip_sandbox_imports (math is a sandbox module) with nothing rebinding
    the names -- the exact "name 'cos' is not defined" failure observed live
    during the MET-642 S3 eval. Also covers the show_object no-op stub."""

    def test_bare_math_functions_are_pre_bound(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script(
                "from math import sin, cos\n"
                "assert (sin(0), cos(0), sqrt(4)) == (0.0, 1.0, 2.0)\n"
                "result = cq.Workplane()",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")

    def test_show_object_is_a_noop(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script(
                "show_object(42)\nresult = cq.Workplane()",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")

    def test_bare_exporters_name_is_pre_bound(self, tmp_path):
        """MET-688: `from cadquery import exporters` is stripped by
        _strip_sandbox_imports (its root "cadquery" is a sandbox module) with
        nothing rebinding `exporters` as a bare name afterward -- the exact
        "name 'exporters' is not defined" failure observed live during a
        Kitchen Table CAD edit. `cq.exporters` is already used internally
        throughout this module with no extra import, so pre-binding it the
        same way as the math convenience names above is safe."""
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script(
                "from cadquery import exporters\n"
                "assert exporters is not None\n"
                "result = cq.Workplane()",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")

    def test_bare_workplane_and_vector_names_are_pre_bound(self, tmp_path):
        """MET-702: same gap as MET-688, found via a real external dataset
        (CAD-Coder, 8.8K CadQuery scripts) rather than a live edit --
        `from cadquery import Workplane, Vector` is stripped the same way,
        with nothing rebinding `Workplane`/`Vector` bare. ~1.3% of the
        dataset's scripts bare-imported a name outside the already-fixed
        set (mostly Workplane/Vector). Rather than add names one at a time
        forever, the fix pre-binds cadquery's entire public top-level API --
        this test checks two representative names, not an exhaustive list,
        since every one of them is already reachable via `cq.<Name>`."""
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCq()),
        ):
            result = ops.execute_script(
                "from cadquery import Workplane, Vector\n"
                "assert Vector is not None\n"
                "result = Workplane()",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")

    def test_bare_cq_name_is_not_shadowed_by_the_internal_cq_submodule(self, tmp_path):
        """`dir(cadquery)` includes the package's own internal `cq` submodule
        under that exact name (`cadquery.cq`) -- naively pre-binding every
        public dir() name would silently replace the `cq` alias every script
        actually expects to mean the top-level package with that unrelated
        submodule. Confirms `cq` still resolves to the patched top-level
        fake, not `getattr(cq, "cq")`."""
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        fake = _FakeCq()
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", fake),
        ):
            result = ops.execute_script(
                "assert cq is not None\nresult = cq.Workplane()",
                str(tmp_path / "out.step"),
            )
        assert result["cad_file"] == str(tmp_path / "out.step")
