"""Unit tests for FreeCAD operations module (MET-221).

All tests mock FreeCAD internals since FreeCAD is not available in CI.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tool_registry.tools.freecad.operations import (
    _SHAPE_DEFAULTS,
    HAS_FREECAD,
    FreecadNotAvailableError,
    FreecadOperations,
)

# ---------------------------------------------------------------------------
# 1. Shape defaults are well-formed
# ---------------------------------------------------------------------------


class TestShapeDefaults:
    """Verify shape defaults cover expected shape types."""

    def test_all_expected_shapes_present(self) -> None:
        expected = {"box", "cylinder", "sphere", "cone", "torus", "bracket", "plate", "enclosure"}
        assert expected == set(_SHAPE_DEFAULTS.keys())

    def test_box_defaults(self) -> None:
        box = _SHAPE_DEFAULTS["box"]
        assert "length" in box
        assert "width" in box
        assert "height" in box

    def test_cylinder_defaults(self) -> None:
        cyl = _SHAPE_DEFAULTS["cylinder"]
        assert "radius" in cyl
        assert "height" in cyl

    def test_bracket_has_hole_radius(self) -> None:
        bracket = _SHAPE_DEFAULTS["bracket"]
        assert "hole_radius" in bracket
        assert "thickness" in bracket


# ---------------------------------------------------------------------------
# 2. FreecadOperations requires FreeCAD
# ---------------------------------------------------------------------------


class TestFreecadGuard:
    """FreeCAD availability checks."""

    def test_require_freecad_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops._require_freecad()

    def test_create_parametric_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.create_parametric("box", {})

    def test_export_step_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.export_step("/input.step")

    def test_generate_mesh_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.generate_mesh("/input.step")

    def test_generate_ic_package_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.generate_ic_package(None, "SOIC", "LM358", {})


class TestIcPinLayout:
    """Pure pin-layout maths (no FreeCAD) for datasheet-driven IC generation."""

    def test_soic_two_sided_numbering(self) -> None:
        pins = FreecadOperations.ic_pin_layout("SOIC", 8, 1.27)
        assert len(pins) == 8
        # 4 per side; pin 1 on y-, pin 8 on y+
        assert {p["side"] for p in pins} == {"y-", "y+"}
        assert pins[0]["pin"] == 1 and pins[0]["side"] == "y-"
        # centred positions: symmetric about 0
        ys = sorted(p["u"] for p in pins if p["side"] == "y-")
        assert ys[0] == pytest.approx(-1.5 * 1.27)
        assert ys[-1] == pytest.approx(1.5 * 1.27)

    def test_qfp_four_sided(self) -> None:
        pins = FreecadOperations.ic_pin_layout("LQFP", 32, 0.8)
        assert len(pins) == 32
        assert {p["side"] for p in pins} == {"y-", "x+", "y+", "x-"}
        # 8 pins per side
        from collections import Counter

        assert set(Counter(p["side"] for p in pins).values()) == {8}

    def test_generate_profile_part_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        prof = [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 10}]
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.generate_profile_part(None, "Shaft", prof, "revolve")

    def test_unsupported_family_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported package family"):
            FreecadOperations.ic_pin_layout("BGA", 64, 0.8)

    def test_lead_count_must_match_sides(self) -> None:
        with pytest.raises(ValueError, match="multiple of"):
            FreecadOperations.ic_pin_layout("SOIC", 7, 1.27)  # odd → invalid for 2 sides
        with pytest.raises(ValueError, match="multiple of"):
            FreecadOperations.ic_pin_layout("QFP", 30, 0.8)  # not divisible by 4


class TestNormalizeProfile:
    """Pure profile validation for revolve/extrude generation (no FreeCAD)."""

    def test_closes_open_profile(self) -> None:
        pts = FreecadOperations.normalize_profile(
            [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 10}], "revolve"
        )
        assert pts[0] == pts[-1]  # auto-closed
        assert len(pts) == 4

    def test_rejects_too_few_points(self) -> None:
        with pytest.raises(ValueError, match="at least 3 points"):
            FreecadOperations.normalize_profile([{"x": 0, "y": 0}, {"x": 1, "y": 1}], "extrude")

    def test_revolve_rejects_negative_radius(self) -> None:
        with pytest.raises(ValueError, match="radius"):
            FreecadOperations.normalize_profile(
                [{"x": -1, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 10}], "revolve"
            )

    def test_extrude_allows_negative_x(self) -> None:
        pts = FreecadOperations.normalize_profile(
            [{"x": -2, "y": 0}, {"x": 2, "y": 0}, {"x": 0, "y": 3}], "extrude"
        )
        assert len(pts) == 4

    def test_unknown_operation_rejected(self) -> None:
        with pytest.raises(ValueError, match="revolve.*extrude|operation"):
            FreecadOperations.normalize_profile(
                [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}], "loft"
            )


# ---------------------------------------------------------------------------
# 3. FreecadOperations init
# ---------------------------------------------------------------------------


class TestFreecadOperationsInit:
    """Initialization and configuration."""

    def test_default_work_dir(self) -> None:
        ops = FreecadOperations()
        assert ops.work_dir == "/workspace"

    def test_custom_work_dir(self) -> None:
        ops = FreecadOperations(work_dir="/custom")
        assert ops.work_dir == "/custom"

    def test_custom_timeout(self) -> None:
        ops = FreecadOperations(timeout=120.0)
        assert ops.timeout == 120.0


# ---------------------------------------------------------------------------
# 4. Build shape dispatching
# ---------------------------------------------------------------------------


class TestBuildShape:
    """Verify _build_shape dispatches to correct FreeCAD Part methods."""

    @pytest.mark.skipif(not HAS_FREECAD, reason="FreeCAD not installed")
    def test_unsupported_shape_raises(self) -> None:
        ops = FreecadOperations()
        with pytest.raises(ValueError, match="Unsupported shape type"):
            ops._build_shape("pentagon", {})

    def test_unsupported_shape_raises_mocked(self) -> None:
        """Test without FreeCAD by calling the dispatch logic directly."""
        ops = FreecadOperations()
        # Patch HAS_FREECAD to True to skip the guard, but the shape is invalid anyway
        with pytest.raises(ValueError, match="Unsupported shape type"):
            ops._build_shape("hexagon", {})


# ---------------------------------------------------------------------------
# 5. Error class
# ---------------------------------------------------------------------------


class TestFreecadNotAvailableError:
    """Error message formatting."""

    def test_error_message(self) -> None:
        err = FreecadNotAvailableError()
        assert "FreeCAD Python bindings" in str(err)
        assert "Docker container" in str(err)


class _FakeVector:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z


class _FakeBoundBox:
    XMin = YMin = ZMin = 0.0
    XMax, YMax, ZMax = 600.0, 400.0, 420.0


class _FakeShape:
    """Mimics a multi-solid ``Part.Shape`` compound read from a STEP file."""

    Volume = 6_080_000.0
    Area = 654_400.0
    BoundBox = _FakeBoundBox()
    # Real Part.Shape has no CenterOfMass (only Part.Solid does) — omit it so
    # a regression back to ``shape.CenterOfMass`` fails loudly with the same
    # AttributeError the real FreeCAD API raises.
    CenterOfGravity = _FakeVector(300.0, 162.105, 365.789)

    def read(self, path: str) -> None:
        self.path = path


class _FakePart:
    @staticmethod
    def Shape() -> _FakeShape:
        return _FakeShape()


class TestGetProperties:
    """freecad.get_properties — center_of_mass must use CenterOfGravity.

    Regression coverage for a real MCP-adapter bug (MET-618): ``Part.Shape``
    has no ``CenterOfMass`` attribute (only ``Part.Solid`` does), so asking
    for it crashed every call with an opaque "Tool execution failed" — the
    AttributeError never reached the caller. ``CenterOfGravity`` is the
    shape-level equivalent FreeCAD actually exposes.
    """

    def test_center_of_mass_uses_center_of_gravity(self) -> None:
        ops = FreecadOperations()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.Part", _FakePart()),
        ):
            result = ops.get_properties("/workspace/part.step", ["center_of_mass"])

        assert result["properties"]["center_of_mass"] == [300.0, 162.105, 365.789]

    def test_all_properties_together(self) -> None:
        ops = FreecadOperations()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.Part", _FakePart()),
        ):
            result = ops.get_properties("/workspace/part.step")

        props = result["properties"]
        assert props["volume"] == 6_080_000.0
        assert props["area"] == 654_400.0
        assert props["center_of_mass"] == [300.0, 162.105, 365.789]
        assert props["bounding_box"] == {
            "min_x": 0.0,
            "min_y": 0.0,
            "min_z": 0.0,
            "max_x": 600.0,
            "max_y": 400.0,
            "max_z": 420.0,
        }


class _FakeDocObject:
    def __init__(self, label: str, shape: _FakeShape | None = None) -> None:
        self.Label = label
        if shape is not None:
            self.Shape = shape


class _FakeDoc:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.Objects: list[_FakeDocObject] = []


class _FakeFreeCADMulti:
    """Fakes the two FreeCAD entry points describe_step_file needs."""

    def __init__(self) -> None:
        self.closed: list[str] = []
        self.doc: _FakeDoc | None = None

    def newDocument(self) -> _FakeDoc:
        self.doc = _FakeDoc("doc1")
        return self.doc

    def closeDocument(self, name: str) -> None:
        self.closed.append(name)


class _FakeImport:
    """Fakes ``Import.insert`` by populating the already-open fake document."""

    def __init__(self, freecad: _FakeFreeCADMulti, objects: list[_FakeDocObject]) -> None:
        self._freecad = freecad
        self._objects = objects

    def insert(self, path: str, docname: str) -> None:
        assert self._freecad.doc is not None
        self._freecad.doc.Objects.extend(self._objects)


class TestDescribeStepFile:
    """freecad.describe_step_file — per-component breakdown (MET-629).

    Regression coverage for the gap where a multipart assembly's individual
    named parts (volume/area/bbox per part) were unreachable over MCP:
    freecad.get_properties only reads the file as one flattened Part.Shape,
    and freecad.execute_code's sandbox blocks any code containing the word
    "open" (e.g. ``Import.open(...)``), which is how an agent naturally
    tried to load the file for a manual per-part inspection.
    """

    def test_returns_named_components_and_skips_shapeless_objects(self) -> None:
        ops = FreecadOperations()
        bbox = _FakeBoundBox()
        tabletop = _FakeDocObject("Tabletop", _FakeShape())
        tabletop.Shape.Solids = [object()]
        tabletop.Shape.Volume = 4_800_000.0
        tabletop.Shape.Area = 480_000.0
        tabletop.Shape.BoundBox = bbox
        leg_a = _FakeDocObject("LegA", _FakeShape())
        leg_a.Shape.Solids = [object()]
        leg_a.Shape.Volume = 640_000.0
        leg_a.Shape.Area = 87_200.0
        leg_a.Shape.BoundBox = bbox
        origin = _FakeDocObject("Origin")  # no Shape attribute at all

        freecad = _FakeFreeCADMulti()
        fake_import = _FakeImport(freecad, [tabletop, leg_a, origin])

        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", freecad),
            patch("tool_registry.tools.freecad.operations.Import", fake_import),
        ):
            result = ops.describe_step_file("/workspace/part.step")

        assert result["file"] == "/workspace/part.step"
        labels = [c["label"] for c in result["components"]]
        assert labels == ["Tabletop", "LegA"]
        assert result["components"][0]["volume"] == 4_800_000.0
        assert result["components"][0]["solid_count"] == 1
        assert freecad.closed == ["doc1"]

    def test_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.describe_step_file("/workspace/part.step")


class TestExecuteCodeSandbox:
    """execute_code source-level guarding is validated before any FreeCAD call,
    so the sandbox policy is testable without FreeCAD bindings (MET-527)."""

    def test_blocks_dangerous_names(self) -> None:
        from tool_registry.tools.freecad.operations import FreecadOperations, ScriptSandboxError

        ops = FreecadOperations()
        for snippet in ("import os", "open('/etc/passwd')", "eval('1')", "__import__('sys')"):
            with pytest.raises(ScriptSandboxError):
                ops.execute_code(None, snippet)

    def test_rejects_oversize_script(self) -> None:
        from tool_registry.tools.freecad.operations import FreecadOperations, ScriptSandboxError

        ops = FreecadOperations()
        with pytest.raises(ScriptSandboxError, match="exceeds"):
            ops.execute_code(None, "\n".join(f"a{i} = {i}" for i in range(201)), max_lines=200)

    def test_blocks_shared_compound_shape_reuse(self) -> None:
        """MET-643: live-caught, then directly repro'd against the real
        adapter (container RestartCount incremented on this exact script) --
        assigning a shape already inside Part.makeCompound(...) to a second
        object's .Shape crashes the FreeCAD/OCCT process outright. Since
        that's a native crash, not a catchable Python exception, it must be
        rejected before exec() ever runs."""
        from tool_registry.tools.freecad.operations import FreecadOperations, ScriptSandboxError

        ops = FreecadOperations()
        code = (
            "tube = Part.makeCylinder(5, 20)\n"
            "plate = Part.makeBox(20, 20, 2)\n"
            "boss = Part.makeCylinder(3, 5)\n"
            "compound = Part.makeCompound([tube, plate, boss])\n"
            "compound_obj = doc.addObject('Part::Feature', 'assembly_compound')\n"
            "compound_obj.Shape = compound\n"
            "arm_obj = doc.addObject('Part::Feature', 'arm')\n"
            "arm_obj.Shape = tube\n"
        )
        with pytest.raises(ScriptSandboxError, match="tube.*makeCompound"):
            ops.execute_code(None, code)

    def test_allows_compound_reuse_after_explicit_copy(self) -> None:
        """The documented safe pattern (MET-643's tool-description guidance):
        calling .copy() before the second assignment must not be flagged."""
        from tool_registry.tools.freecad.operations import FreecadOperations

        ops = FreecadOperations()
        code = (
            "tube = Part.makeCylinder(5, 20)\n"
            "compound = Part.makeCompound([tube])\n"
            "compound_obj = doc.addObject('Part::Feature', 'assembly_compound')\n"
            "compound_obj.Shape = compound\n"
            "tube = tube.copy()\n"
            "arm_obj = doc.addObject('Part::Feature', 'arm')\n"
            "arm_obj.Shape = tube\n"
        )
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            from tool_registry.tools.freecad.operations import FreecadNotAvailableError

            # Past the sandbox guard, execute_code hits the (expected) missing-
            # FreeCAD error -- proving the compound-reuse check did NOT fire.
            with pytest.raises(FreecadNotAvailableError):
                ops.execute_code(None, code)

    def test_does_not_flag_unrelated_shape_assignments(self) -> None:
        """Ordinary .Shape assignments with no compound involvement are untouched."""
        from tool_registry.tools.freecad.operations import FreecadOperations

        ops = FreecadOperations()
        code = (
            "box = Part.makeBox(1, 1, 1)\n"
            "obj = doc.addObject('Part::Feature', 'box')\n"
            "obj.Shape = box\n"
        )
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            from tool_registry.tools.freecad.operations import FreecadNotAvailableError

            with pytest.raises(FreecadNotAvailableError):
                ops.execute_code(None, code)


class _FakeVector:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x, self.y, self.z = x, y, z


class _FakeFreeCADModule:
    """Minimal fake exposing exactly what execute_code's namespace needs."""

    Vector = _FakeVector
    Rotation = object
    Placement = object
    Matrix = object


class _FakeDocForExec:
    def __init__(self) -> None:
        self.recomputed = False

    def recompute(self) -> None:
        self.recomputed = True


class TestExecuteCodeNamespaceConvenienceNames:
    """MET-645: bare Vector/Rotation/Placement/Matrict must be pre-bound so a
    model doesn't need to know to write FreeCAD.Vector -- the exact failure
    ("name 'Vector' is not defined") observed live during the MET-642 eval."""

    def test_bare_vector_is_pre_bound(self) -> None:
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(doc, "result = Vector(1, 2, 3)")
        assert isinstance(result, _FakeVector)
        assert (result.x, result.y, result.z) == (1, 2, 3)
        assert doc.recomputed is True

    def test_bare_rotation_placement_matrix_are_pre_bound(self) -> None:
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(doc, "result = (Rotation, Placement, Matrix)\nresult = 'ok'")
        assert result == "ok"

    def test_bare_math_functions_are_pre_bound(self) -> None:
        """MET-649: `from math import sin, cos` is stripped by
        _strip_sandbox_imports (math is a sandbox module) with nothing
        rebinding the names -- the exact "name 'cos' is not defined" failure
        observed live during the MET-642 S3 eval."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(
                doc, "from math import sin, cos\nresult = (sin(0), cos(0), sqrt(4), pi)"
            )
        assert result == (0.0, 1.0, 2.0, pytest.approx(3.14159, rel=1e-3))

    def test_show_object_is_a_noop(self) -> None:
        """MET-649: show_object is a common CQ-editor/CQGI convention the
        model sometimes hallucinates; it must not raise NameError."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(doc, "show_object(42)\nresult = 'ok'")
        assert result == "ok"


class _FakeExecCodeResultShape:
    pass


class _FakeFreecadObject:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.Shape = _FakeExecCodeResultShape()


class TestExecuteCodeResultDictFallback:
    """MET-687: a script writing ``result = {'obj_id': model.Name, ...}``
    (a description of the object) instead of the object itself must still
    resolve to a real, registerable object -- confirmed live: the adapter's
    caller only registers a ``result`` with a ``.Shape`` attribute, so a
    dict silently vanished, leaving a script-claimed obj_id nothing could
    later commit-by-reference against."""

    def test_dict_result_with_obj_id_resolves_to_the_real_object(self) -> None:
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        doc.Objects = [_FakeFreecadObject("Kitchen_Table_v1_Modified")]
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(
                doc,
                "result = {'obj_id': 'Kitchen_Table_v1_Modified', 'leg_count': 6}",
            )
        assert result is doc.Objects[0]
        assert hasattr(result, "Shape")

    def test_dict_result_with_unresolvable_name_returns_dict_unchanged(self) -> None:
        """No matching object -- fall through to the dict as-is (a genuinely
        broken script) rather than raising or fabricating an object."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        doc.Objects = [_FakeFreecadObject("SomethingElse")]
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(doc, "result = {'obj_id': 'NoSuchObject'}")
        assert result == {"obj_id": "NoSuchObject"}

    def test_object_result_is_returned_unchanged(self) -> None:
        """The documented, correct contract (result IS the object) must keep
        working exactly as before -- this fallback is additive only."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        doc.Objects = []
        obj = _FakeFreecadObject("Direct")
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            namespace_result = ops._resolve_execute_code_result(doc, obj)
        assert namespace_result is obj

    def test_logs_script_before_running_it(self) -> None:
        """MET-643: a native crash inside exec() kills the adapter process
        with no Python traceback ever logged, leaving no way to tell what
        script provoked it. The script must be logged BEFORE exec runs (a
        log emitted after a crash would never be reached)."""
        import structlog.testing

        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
            structlog.testing.capture_logs() as logs,
        ):
            ops.execute_code(doc, "result = 'ok'")
        events = [entry for entry in logs if entry.get("event") == "freecad_execute_code_running"]
        assert len(events) == 1
        assert events[0]["code"] == "result = 'ok'"

    def test_missing_convenience_attr_is_skipped_not_a_crash(self) -> None:
        """A FreeCAD binding lacking one of these names must not break every
        execute_code call -- degrade gracefully rather than AttributeError."""

        class _BareFreeCAD:
            pass

        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _BareFreeCAD()),
        ):
            result = ops.execute_code(doc, "result = 'still works'")
        assert result == "still works"


class TestExecuteCodeSandboxedImport:
    """MET-645 follow-up: found live during the MET-642 re-eval -- a
    model-written import statement using a syntax variant
    _strip_sandbox_imports' regex doesn't recognize (e.g. a dotted submodule)
    reached exec() untouched and crashed with "__import__ not found" because
    the restricted __builtins__ never had one. A real, restricted __import__
    now backs any import/from-import syntax, resolving only to the
    already-injected sandbox modules."""

    def test_dotted_submodule_import_no_longer_crashes(self) -> None:
        """The exact failure mode observed live: `import FreeCAD.Base` isn't
        matched by _IMPORT_RE (its \\w+ group can't contain a dot), so
        previously this reached exec() with no __import__ available."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(doc, "import FreeCAD.Base\nresult = 'ok'")
        assert result == "ok"

    def test_comma_separated_import_line_resolves(self) -> None:
        """`import Part, math` isn't matched by _IMPORT_RE (it expects a single
        bare \\w+, not a comma list), so this line also reaches exec()
        untouched -- another real syntax variant the regex misses."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(doc, "import Part, math\nresult = math.pi")
        assert result == pytest.approx(3.141592653589793)

    def test_import_of_disallowed_module_raises_import_error(self) -> None:
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            with pytest.raises(RuntimeError, match="import of 'json' is not permitted"):
                ops.execute_code(doc, "import json\nresult = json.dumps({})")

    def test_explicit_dunder_import_call_still_blocked_pre_exec(self) -> None:
        """The restricted __import__ backs implicit import statements only --
        an explicit literal __import__(...) call is still caught by the
        pre-exec sandbox-policy check, same as before this fix."""
        from tool_registry.tools.freecad.operations import ScriptSandboxError

        ops = FreecadOperations()
        with pytest.raises(ScriptSandboxError, match="__import__"):
            ops.execute_code(None, "result = __import__('math')")

    def test_import_module_is_allowed_and_bare_name_pre_bound(self) -> None:
        """MET-688: found live -- a script correctly reached for FreeCAD's
        Import module (the one that preserves STEP Labels/multi-part
        structure, per MET-616) to re-load a work product, and got
        "import of 'Import' is not permitted in this sandbox", forcing a
        fallback to Part.Shape().read() (which flattens to one anonymous
        shape). Import grants no capability Part doesn't already have (both
        do file I/O through FreeCAD's native layer regardless of the
        sandbox's blocked Python builtins) -- the real isolation boundary is
        the container, per this module's own existing design note."""
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        fake_import_module = SimpleNamespace(insert=lambda *a, **k: None)
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
            patch("tool_registry.tools.freecad.operations.Import", fake_import_module),
        ):
            # Bare name (no import statement needed, matching FreeCAD/Part/math).
            result = ops.execute_code(doc, "result = Import is not None")
            assert result is True
            # Explicit `import Import` statement must also resolve, not raise.
            result = ops.execute_code(doc, "import Import\nresult = Import is not None")
            assert result is True


class TestExecuteCodeExceptionTypes:
    """Exception types were entirely missing from _SAFE_BUILTINS -- a pure
    oversight (unlike open/eval/__import__, catching/raising a builtin
    exception type grants no capability). A script wrapping its own logic in
    try/except (a completely normal pattern) crashed with "name 'Exception'
    is not defined" -- found live during the MET-642 S3 eval."""

    def test_try_except_exception_no_longer_crashes(self) -> None:
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(
                doc,
                "try:\n    raise ValueError('x')\nexcept Exception as e:\n    result = str(e)\n",
            )
        assert result == "x"

    def test_common_exception_types_available(self) -> None:
        ops = FreecadOperations()
        doc = _FakeDocForExec()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.FreeCAD", _FakeFreeCADModule()),
        ):
            result = ops.execute_code(
                doc,
                "result = (Exception, ValueError, TypeError, KeyError, "
                "IndexError, AttributeError, RuntimeError)\nresult = 'ok'",
            )
        assert result == "ok"
