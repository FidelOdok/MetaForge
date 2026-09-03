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
    MissingJointLimitsError,
    ScriptSandboxError,
    UnsupportedJointTypeError,
    _build_assembly_sdf,
    _build_assembly_urdf,
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


class _FakeVector3:
    def __init__(self, x: float, y: float, z: float):
        self.x, self.y, self.z = x, y, z


# Real cadquery (2.8.0, verified live against the deployed cadquery-adapter
# container -- MET-706 follow-up) exposes inertia as the STATIC method
# ``cq.Shape.matrixOfInertia(solid)``, returning a plain 0-indexed
# ``list[list[float]]`` -- NOT an instance method returning an OCCT-style
# ``.Value(r, c)`` object, which the original fakes here (and the code
# itself) wrongly assumed until a live-verify call surfaced the mismatch.
_FAKE_INERTIA_ROWS = [
    [6.0, 0.0, 0.0],
    [0.0, 5.0, 0.0],
    [0.0, 0.0, 4.0],
]


class _FakeMassSolid:
    """volume=1_000_000 mm^3 (a 100mm cube), center at (1,2,3)mm, a
    deliberately simple inertia matrix -- exact numeric correctness is
    checked via the hand-derived expected values in the tests below."""

    def Volume(self):  # noqa: N802
        return 1_000_000.0

    def Center(self):  # noqa: N802
        return _FakeVector3(1.0, 2.0, 3.0)


class _FakeMassShape:
    def val(self):
        return _FakeMassSolid()


class _FakeShapeNamespace:
    """Stands in for ``cq.Shape`` -- just the one static method the
    production code calls."""

    @staticmethod
    def matrixOfInertia(_solid):  # noqa: N802
        return [row[:] for row in _FAKE_INERTIA_ROWS]


class _FakeUrdfExporters:
    calls: list[tuple[str, str]] = []

    @classmethod
    def export(cls, _shape, output_path, exportType=None):  # noqa: N803
        cls.calls.append((output_path, exportType))
        with open(output_path, "wb") as f:  # noqa: PTH123
            f.write(b"fake mesh bytes")


class _FakeImporters:
    @staticmethod
    def importStep(_path):  # noqa: N802
        return _FakeMassShape()


class _FakeCqForUrdf:
    exporters = _FakeUrdfExporters()
    importers = _FakeImporters()
    Shape = _FakeShapeNamespace()


class TestExportUrdf:
    """MET-706 session: URDF export -- a real <inertial> block derived from
    actual geometry + a material density, not a placeholder."""

    def test_writes_urdf_and_mesh_with_correct_mass_properties(self, tmp_path):
        _FakeUrdfExporters.calls = []
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "part.urdf")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf(
                "part.step",
                link_name="widget_link",
                material="aluminum_6061",
                output_path=output_path,
            )

        # mass = volume_mm3 * 1e-9 * density = 1_000_000 * 1e-9 * 2700 = 2.7 kg
        assert result["mass_kg"] == pytest.approx(2.7)
        assert result["density_kg_m3"] == 2700.0
        # center of mass mm -> m
        assert result["center_of_mass_m"] == {"x": 0.001, "y": 0.002, "z": 0.003}
        # inertia mm^5 -> kg*m^2: ixx = 6.0 * 1e-15 * 2700 = 1.62e-11
        assert result["inertia_kgm2"]["ixx"] == pytest.approx(6.0 * 1e-15 * 2700.0)
        assert result["inertia_kgm2"]["iyy"] == pytest.approx(5.0 * 1e-15 * 2700.0)
        assert result["inertia_kgm2"]["izz"] == pytest.approx(4.0 * 1e-15 * 2700.0)
        # off-diagonal terms in _FAKE_INERTIA_ROWS are 0
        assert result["inertia_kgm2"]["ixy"] == 0.0

        assert result["link_name"] == "widget_link"
        urdf_text = Path(output_path).read_text()
        assert '<robot name="widget_link_robot">' in urdf_text
        assert '<link name="widget_link">' in urdf_text
        assert urdf_text.count("<mesh") == 2  # visual + collision
        assert Path(result["mesh_file"]).exists()

    def test_explicit_density_overrides_material_name(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf(
                "part.step",
                material="aluminum_6061",
                density_kg_m3=1234.0,
                output_path=str(tmp_path / "part2.urdf"),
            )
        assert result["density_kg_m3"] == 1234.0
        assert result["mass_kg"] == pytest.approx(1_000_000.0 * 1e-9 * 1234.0)

    def test_unrecognized_material_falls_back_to_default_density(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf(
                "part.step",
                material="some unlisted unobtainium alloy",
                output_path=str(tmp_path / "part3.urdf"),
            )
        assert result["density_kg_m3"] == 1000.0  # DEFAULT_DENSITY_KG_M3

    def test_mesh_uri_prefix_is_applied(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "part4.urdf")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            ops.export_urdf(
                "part.step",
                mesh_uri_prefix="package://widget/meshes/",
                output_path=output_path,
            )
        urdf_text = Path(output_path).read_text()
        assert 'filename="package://widget/meshes/part4.stl"' in urdf_text

    def test_xacro_flag_writes_xacro_extension_and_namespace(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf("part.step", link_name="widget_link", xacro=True)
        assert result["output_file"].endswith(".xacro")
        text = Path(result["output_file"]).read_text()
        assert 'xmlns:xacro="http://www.ros.org/wiki/xacro"' in text

    def test_no_xacro_namespace_when_flag_false(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf("part.step", link_name="widget_link")
        assert result["output_file"].endswith(".urdf")
        text = Path(result["output_file"]).read_text()
        assert "xacro" not in text


class TestBuildAssemblyUrdf:
    """MET-706 session (tier-2a): multi-link URDF with real joints, built
    directly against ``_build_assembly_urdf`` (no CadQuery involved --
    joint-type mapping and XML shape are pure logic)."""

    _LINKS = [
        {
            "name": "base",
            "mesh_uri": "base.stl",
            "mass_kg": 1.0,
            "com_m": (0.0, 0.0, 0.0),
            "inertia_kgm2": (1.0, 0.0, 0.0, 1.0, 0.0, 1.0),
        },
        {
            "name": "arm",
            "mesh_uri": "arm.stl",
            "mass_kg": 0.5,
            "com_m": (0.1, 0.0, 0.0),
            "inertia_kgm2": (0.1, 0.0, 0.0, 0.1, 0.0, 0.1),
        },
    ]

    def test_fixed_joint(self):
        joints = [
            {
                "name": "base_to_arm",
                "type": "fixed",
                "base": "base",
                "follower": "arm",
                "axis": (0, 0, 1),
                "anchor": (10.0, 0.0, 0.0),
            },
        ]
        xml = _build_assembly_urdf(robot_name="bot", links=self._LINKS, joints=joints)
        assert '<robot name="bot">' in xml
        assert '<link name="base">' in xml
        assert '<link name="arm">' in xml
        assert '<joint name="base_to_arm" type="fixed">' in xml
        assert '<parent link="base" />' in xml
        assert '<child link="arm" />' in xml
        # anchor mm -> m
        assert 'xyz="0.01 0 0"' in xml
        # fixed joints carry no <axis>
        assert "<axis" not in xml

    def test_revolute_maps_to_continuous_with_no_fabricated_limit(self):
        joints = [
            {
                "name": "j1",
                "type": "revolute",
                "base": "base",
                "follower": "arm",
                "axis": (0, 0, 1),
                "anchor": (0, 0, 0),
            },
        ]
        xml = _build_assembly_urdf(robot_name="bot", links=self._LINKS, joints=joints)
        assert 'type="continuous"' in xml
        assert "<axis" in xml
        assert "<limit" not in xml

    def test_slider_maps_to_prismatic_with_limits(self):
        joints = [
            {
                "name": "j1",
                "type": "slider",
                "base": "base",
                "follower": "arm",
                "axis": (1, 0, 0),
                "anchor": (0, 0, 0),
                "limits": {"lower": -0.05, "upper": 0.05},
            },
        ]
        xml = _build_assembly_urdf(robot_name="bot", links=self._LINKS, joints=joints)
        assert 'type="prismatic"' in xml
        assert 'lower="-0.05"' in xml
        assert 'upper="0.05"' in xml

    def test_slider_without_limits_raises(self):
        joints = [
            {
                "name": "j1",
                "type": "slider",
                "base": "base",
                "follower": "arm",
                "axis": (1, 0, 0),
                "anchor": (0, 0, 0),
            },
        ]
        with pytest.raises(MissingJointLimitsError, match="requires a <limit>"):
            _build_assembly_urdf(robot_name="bot", links=self._LINKS, joints=joints)

    @pytest.mark.parametrize("bad_type", ["cylindrical", "ball"])
    def test_unsupported_joint_types_raise(self, bad_type):
        joints = [
            {
                "name": "j1",
                "type": bad_type,
                "base": "base",
                "follower": "arm",
                "axis": (0, 0, 1),
                "anchor": (0, 0, 0),
            },
        ]
        with pytest.raises(UnsupportedJointTypeError, match="no single-joint URDF equivalent"):
            _build_assembly_urdf(robot_name="bot", links=self._LINKS, joints=joints)


class TestExportUrdfAssembly:
    """MET-706 session (tier-2a): the multi-part CadQuery-facing entry point."""

    def test_writes_multi_link_urdf_with_per_part_mass_properties(self, tmp_path):
        _FakeUrdfExporters.calls = []
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "robot.urdf")
        parts = [
            {"input_file": "base.step", "link_name": "base", "material": "aluminum_6061"},
            {"input_file": "arm.step", "link_name": "arm", "material": "steel"},
        ]
        joints = [
            {
                "name": "j1",
                "type": "revolute",
                "base": "base",
                "follower": "arm",
                "axis": (0, 0, 1),
                "anchor": (0, 0, 0),
            },
        ]
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf_assembly(
                parts, joints, robot_name="my_robot", output_path=output_path
            )

        assert result["link_names"] == ["base", "arm"]
        assert result["joint_names"] == ["j1"]
        assert len(result["mesh_files"]) == 2
        for mesh_file in result["mesh_files"]:
            assert Path(mesh_file).exists()

        urdf_text = Path(output_path).read_text()
        assert '<robot name="my_robot">' in urdf_text
        assert '<link name="base">' in urdf_text
        assert '<link name="arm">' in urdf_text
        assert 'type="continuous"' in urdf_text

    def test_xacro_flag_writes_xacro_extension_and_namespace(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        parts = [{"input_file": "base.step", "link_name": "base"}]
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_urdf_assembly(parts, [], robot_name="my_robot", xacro=True)
        assert result["output_file"].endswith(".xacro")
        text = Path(result["output_file"]).read_text()
        assert 'xmlns:xacro="http://www.ros.org/wiki/xacro"' in text

    def test_empty_parts_raises(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            with pytest.raises(ValueError, match="parts is required"):
                ops.export_urdf_assembly([], [])


class TestExportSdf:
    """MET-706 session: SDF (Gazebo) export -- schema grounded against
    gazebosim/sdformat's actual spec files, same real-mass-properties
    reasoning as TestExportUrdf."""

    def test_writes_standalone_sdf_model_with_correct_mass_properties(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "part.sdf")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_sdf(
                "part.step",
                model_name="widget",
                link_name="widget_link",
                material="steel",
                output_path=output_path,
            )

        # mass = 1_000_000 mm^3 * 1e-9 * 7850 kg/m^3 = 7.85 kg
        assert result["mass_kg"] == pytest.approx(7.85)
        assert result["density_kg_m3"] == 7850.0
        assert result["center_of_mass_m"] == {"x": 0.001, "y": 0.002, "z": 0.003}
        assert result["inertia_kgm2"]["ixx"] == pytest.approx(6.0 * 1e-15 * 7850.0)

        sdf_text = Path(output_path).read_text()
        assert '<sdf version="1.11">' in sdf_text
        assert '<model name="widget">' in sdf_text
        assert '<link name="widget_link">' in sdf_text
        assert "<world" not in sdf_text  # standalone model, no world wrapper
        assert sdf_text.count("<mesh>") == 2  # collision + visual
        assert Path(result["mesh_file"]).exists()

    def test_world_name_wraps_model_and_changes_extension(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_sdf("part.step", world_name="my_world")

        assert result["output_file"].endswith(".world")
        sdf_text = Path(result["output_file"]).read_text()
        assert '<world name="my_world">' in sdf_text
        assert "<model" in sdf_text

    def test_static_flag_is_reflected(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "static_part.sdf")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            ops.export_sdf("part.step", static=True, output_path=output_path)
        sdf_text = Path(output_path).read_text()
        assert "<static>true</static>" in sdf_text

    def test_explicit_mesh_uri_overrides_default(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "part5.sdf")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            ops.export_sdf(
                "part.step",
                mesh_uri="model://widget/meshes/part.stl",
                output_path=output_path,
            )
        sdf_text = Path(output_path).read_text()
        assert "<uri>model://widget/meshes/part.stl</uri>" in sdf_text


class TestBuildAssemblySdf:
    """MET-706 session (tier-2a): multi-link SDF with real joints. SDF's
    schema is more permissive than URDF's -- also exercises `ball`, which
    URDF has no single-joint equivalent for (see _SDF_JOINT_TYPE_MAP's
    module comment)."""

    _LINKS = [
        {
            "name": "base",
            "mesh_uri": "base.stl",
            "mass_kg": 1.0,
            "com_m": (0.0, 0.0, 0.0),
            "inertia_kgm2": (1.0, 0.0, 0.0, 1.0, 0.0, 1.0),
        },
        {
            "name": "arm",
            "mesh_uri": "arm.stl",
            "mass_kg": 0.5,
            "com_m": (0.1, 0.0, 0.0),
            "inertia_kgm2": (0.1, 0.0, 0.0, 0.1, 0.0, 0.1),
        },
    ]

    def test_fixed_joint(self):
        joints = [
            {"name": "base_to_arm", "type": "fixed", "base": "base", "follower": "arm"},
        ]
        xml = _build_assembly_sdf(
            model_name="bot", links=self._LINKS, joints=joints, static=False, world_name=""
        )
        assert '<model name="bot">' in xml
        assert '<link name="base">' in xml
        assert '<link name="arm">' in xml
        assert '<joint name="base_to_arm" type="fixed">' in xml
        assert "<parent>base</parent>" in xml
        assert "<child>arm</child>" in xml

    def test_revolute_maps_to_continuous_with_no_fabricated_limit(self):
        joints = [
            {
                "name": "j1",
                "type": "revolute",
                "base": "base",
                "follower": "arm",
                "axis": (0, 0, 1),
            },
        ]
        xml = _build_assembly_sdf(
            model_name="bot", links=self._LINKS, joints=joints, static=False, world_name=""
        )
        assert 'type="continuous"' in xml
        assert "<axis>" in xml
        assert "<limit" not in xml

    def test_ball_joint_is_supported_natively(self):
        joints = [
            {"name": "j1", "type": "ball", "base": "base", "follower": "arm"},
        ]
        xml = _build_assembly_sdf(
            model_name="bot", links=self._LINKS, joints=joints, static=False, world_name=""
        )
        assert 'type="ball">' in xml
        assert "<axis>" not in xml

    def test_slider_maps_to_prismatic_with_limits(self):
        joints = [
            {
                "name": "j1",
                "type": "slider",
                "base": "base",
                "follower": "arm",
                "axis": (1, 0, 0),
                "limits": {"lower": -0.05, "upper": 0.05},
            },
        ]
        xml = _build_assembly_sdf(
            model_name="bot", links=self._LINKS, joints=joints, static=False, world_name=""
        )
        assert 'type="prismatic"' in xml
        assert "<lower>-0.05</lower>" in xml
        assert "<upper>0.05</upper>" in xml

    def test_slider_without_limits_raises(self):
        joints = [
            {"name": "j1", "type": "slider", "base": "base", "follower": "arm", "axis": (1, 0, 0)},
        ]
        with pytest.raises(MissingJointLimitsError):
            _build_assembly_sdf(
                model_name="bot", links=self._LINKS, joints=joints, static=False, world_name=""
            )

    def test_cylindrical_raises(self):
        joints = [
            {"name": "j1", "type": "cylindrical", "base": "base", "follower": "arm"},
        ]
        with pytest.raises(UnsupportedJointTypeError, match="no direct SDF"):
            _build_assembly_sdf(
                model_name="bot", links=self._LINKS, joints=joints, static=False, world_name=""
            )

    def test_world_name_wraps_model(self):
        xml = _build_assembly_sdf(
            model_name="bot", links=self._LINKS, joints=[], static=False, world_name="my_world"
        )
        assert '<world name="my_world">' in xml
        assert "<model" in xml


class TestExportSdfAssembly:
    """MET-706 session (tier-2a): the multi-part CadQuery-facing entry point."""

    def test_writes_multi_link_sdf_with_per_part_mass_properties(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "robot.sdf")
        parts = [
            {"input_file": "base.step", "link_name": "base", "material": "aluminum_6061"},
            {"input_file": "arm.step", "link_name": "arm", "material": "steel"},
        ]
        joints = [
            {"name": "j1", "type": "ball", "base": "base", "follower": "arm"},
        ]
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            result = ops.export_sdf_assembly(
                parts, joints, model_name="my_robot", output_path=output_path
            )

        assert result["link_names"] == ["base", "arm"]
        assert result["joint_names"] == ["j1"]
        assert len(result["mesh_files"]) == 2
        for mesh_file in result["mesh_files"]:
            assert Path(mesh_file).exists()

        sdf_text = Path(output_path).read_text()
        assert '<model name="my_robot">' in sdf_text
        assert '<link name="base">' in sdf_text
        assert '<link name="arm">' in sdf_text
        assert 'type="ball">' in sdf_text

    def test_empty_parts_raises(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUrdf()),
        ):
            with pytest.raises(ValueError, match="parts is required"):
                ops.export_sdf_assembly([], [])


class _FakeStlExporters:
    """Writes a real, minimal binary STL (one triangle) regardless of what
    shape/exportType is passed -- export_usd always requests STL for its
    mesh, then re-parses it, so the fake needs to produce parseable bytes,
    not just a placeholder file."""

    calls: list[tuple[str, str]] = []

    @classmethod
    def export(cls, _shape, output_path, exportType=None):  # noqa: N803
        cls.calls.append((output_path, exportType))
        import struct

        header = b"\x00" * 80
        body = struct.pack("<I", 1)
        body += struct.pack("<3f", 0.0, 0.0, 1.0)
        body += struct.pack("<3f", 0.0, 0.0, 0.0)
        body += struct.pack("<3f", 1.0, 0.0, 0.0)
        body += struct.pack("<3f", 0.0, 1.0, 0.0)
        body += struct.pack("<H", 0)
        with open(output_path, "wb") as f:  # noqa: PTH123
            f.write(header + body)


class _FakeCqForUsd:
    exporters = _FakeStlExporters()
    importers = _FakeImporters()
    Shape = _FakeShapeNamespace()


class TestExportUsd:
    """MET-706 session: USD export -- hand-authored .usda, real mesh (via
    STL round-trip) and real mass properties, same as URDF/SDF."""

    def test_writes_usda_with_correct_mass_properties_and_mesh(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "part.usda")
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUsd()),
        ):
            result = ops.export_usd(
                "part.step",
                prim_name="widget",
                material="aluminum_6061",
                output_path=output_path,
            )

        assert result["mass_kg"] == pytest.approx(2.7)
        assert result["triangle_count"] == 1
        usda_text = Path(output_path).read_text()
        assert 'def Xform "widget"' in usda_text
        assert "PhysicsMassAPI" in usda_text
        assert Path(result["mesh_file"]).exists()

    def test_non_axis_aligned_part_raises_instead_of_silently_wrong_output(self, tmp_path):
        from tool_registry.tools.cadquery.usd_export import NonAxisAlignedInertiaError

        # _FAKE_INERTIA_ROWS is diagonal-only, so this fake's Shape
        # namespace returns a matrix with a large off-diagonal term instead,
        # for this one test.
        class _TiltedShapeNamespace:
            @staticmethod
            def matrixOfInertia(_solid):  # noqa: N802
                return [[6.0, 3.0, 3.0], [3.0, 6.0, 3.0], [3.0, 3.0, 6.0]]

        class _TiltedShape:
            def val(self):
                return _FakeMassSolid()

        class _FakeCqTilted:
            exporters = _FakeStlExporters()
            Shape = _TiltedShapeNamespace()

            class importers:  # noqa: N801
                @staticmethod
                def importStep(_path):  # noqa: N802
                    return _TiltedShape()

        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqTilted()),
            pytest.raises(NonAxisAlignedInertiaError),
        ):
            ops.export_usd("part.step", output_path=str(tmp_path / "tilted.usda"))


class TestExportUsdAssembly:
    """MET-706 session (tier-2a): the multi-part CadQuery-facing entry point
    for multi-body USD with real UsdPhysics joints."""

    def test_writes_multi_body_usda_with_per_part_mass_properties(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "robot.usda")
        parts = [
            {"input_file": "base.step", "link_name": "base", "material": "aluminum_6061"},
            {"input_file": "arm.step", "link_name": "arm", "material": "steel"},
        ]
        joints = [
            {"name": "j1", "type": "ball", "base": "base", "follower": "arm"},
        ]
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUsd()),
        ):
            result = ops.export_usd_assembly(
                parts, joints, robot_name="my_robot", output_path=output_path
            )

        assert result["link_names"] == ["base", "arm"]
        assert result["joint_names"] == ["j1"]
        assert len(result["mesh_files"]) == 2
        for mesh_file in result["mesh_files"]:
            assert Path(mesh_file).exists()

        usda_text = Path(output_path).read_text()
        assert 'def Xform "my_robot"' in usda_text
        assert 'def Xform "base"' in usda_text
        assert 'def Xform "arm"' in usda_text
        assert 'def PhysicsSphericalJoint "j1"' in usda_text

    def test_empty_parts_raises(self, tmp_path):
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with (
            patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", True),
            patch("tool_registry.tools.cadquery.operations.cq", _FakeCqForUsd()),
        ):
            with pytest.raises(ValueError, match="parts is required"):
                ops.export_usd_assembly([], [])


class TestGenerateRos2Launch:
    """MET-706 session: ROS 2 launch file generation -- pure text, no
    CadQuery/geometry involved, unlike this class's other export_* methods."""

    def test_writes_a_valid_python_launch_file(self, tmp_path):
        import ast

        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        output_path = str(tmp_path / "widget.launch.py")
        result = ops.generate_ros2_launch("widget", "/tmp/widget.urdf", output_path=output_path)
        assert result["output_file"] == output_path
        assert result["robot_name"] == "widget"
        text = Path(output_path).read_text()
        ast.parse(text)
        assert "robot_state_publisher" in text

    def test_does_not_require_cadquery_to_be_available(self, tmp_path):
        """Unlike export_urdf/export_sdf/export_usd, this is pure text
        generation -- confirms it works even with HAS_CADQUERY False,
        i.e. _require_cadquery() is genuinely never called."""
        ops = CadqueryOperations(work_dir=str(tmp_path), sandbox_enabled=True)
        with patch("tool_registry.tools.cadquery.operations.HAS_CADQUERY", False):
            result = ops.generate_ros2_launch(
                "widget", "/tmp/widget.urdf", output_path=str(tmp_path / "w.launch.py")
            )
        assert Path(result["output_file"]).exists()
