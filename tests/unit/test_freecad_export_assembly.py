"""Assembly export resolution (MET-10) and STEP export (MET-616).

`export_model` on an `App::Part` assembly used to crash with
`'App.Part' object has no attribute 'Shape'` — the container has no `.Shape` of
its own. `_shape_leaves` flattens a container into its child leaf shapes so the
read-only measure/describe path can build a compound (MET-10).

`export_object_step_bytes` (the STEP export path) does NOT use that compound:
FreeCAD's raw `Shape.exportStep()` has no concept of Labels, so exporting a
flattened compound collapsed every assembly into one anonymous STEP PRODUCT
(MET-616). It instead passes the live object straight to the document-aware
`Import.export()`, which writes one PRODUCT per object and preserves the
assembly hierarchy.

FreeCAD isn't in CI, so these exercise the pure traversal / call shape with
duck-typed fakes (no FreeCAD, no `Part`, no `Import`).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tool_registry.tools.freecad.operations import FreecadNotAvailableError, FreecadOperations


class _Shape:
    """Stand-in for a Part shape; only `isNull()` is used by the traversal."""

    def __init__(self, null: bool = False) -> None:
        self._null = null

    def isNull(self) -> bool:
        return self._null


def _leaf(shape: _Shape) -> SimpleNamespace:
    """A shape-bearing object (Part::Feature / primitive / body with a feature)."""
    return SimpleNamespace(Shape=shape, Label="leaf")


def _part(*children: object, label: str = "Assembly") -> SimpleNamespace:
    """An `App::Part`-like container: no `.Shape`, children under `.Group`."""
    return SimpleNamespace(Group=list(children), Label=label)


@pytest.fixture()
def ops() -> FreecadOperations:
    return FreecadOperations()


class TestShapeLeaves:
    def test_leaf_returns_its_own_shape(self, ops: FreecadOperations) -> None:
        s = _Shape()
        assert ops._shape_leaves(_leaf(s)) == [s]

    def test_assembly_flattens_to_child_shapes(self, ops: FreecadOperations) -> None:
        a, b = _Shape(), _Shape()
        assembly = _part(_leaf(a), _leaf(b))
        assert ops._shape_leaves(assembly) == [a, b]

    def test_empty_shapes_are_skipped(self, ops: FreecadOperations) -> None:
        good = _Shape()
        empty_body = _leaf(_Shape(null=True))  # body with no features yet
        assert ops._shape_leaves(_part(_leaf(good), empty_body)) == [good]

    def test_nested_containers_flatten(self, ops: FreecadOperations) -> None:
        a, b, c = _Shape(), _Shape(), _Shape()
        inner = _part(_leaf(b), _leaf(c), label="Inner")
        outer = _part(_leaf(a), inner, label="Outer")
        assert ops._shape_leaves(outer) == [a, b, c]

    def test_cycle_is_guarded(self, ops: FreecadOperations) -> None:
        s = _Shape()
        container = _part(_leaf(s))
        container.Group.append(container)  # self-reference must not loop forever
        assert ops._shape_leaves(container) == [s]

    def test_app_link_is_followed(self, ops: FreecadOperations) -> None:
        s = _Shape()
        link = SimpleNamespace(LinkedObject=_leaf(s), Label="Link")
        assert ops._shape_leaves(link) == [s]

    def test_empty_assembly_yields_nothing(self, ops: FreecadOperations) -> None:
        assert ops._shape_leaves(_part()) == []


class TestResolveShape:
    def test_requires_freecad(self, ops: FreecadOperations) -> None:
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops._resolve_shape(_leaf(_Shape()))

    def test_single_leaf_resolves_to_its_shape(self, ops: FreecadOperations) -> None:
        s = _Shape()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True):
            assert ops._resolve_shape(_leaf(s)) is s

    def test_empty_assembly_raises_clear_error(self, ops: FreecadOperations) -> None:
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True):
            with pytest.raises(ValueError, match="no exportable geometry"):
                ops._resolve_shape(_part(label="EmptyRig"))


class _FakeImport:
    """Records the object list `Import.export` was called with and writes a
    marker file, so tests can assert on both without real FreeCAD."""

    def __init__(self, written: bytes = b"STEP;fake-export;MANIFOLD_SOLID_BREP") -> None:
        self.calls: list[list[object]] = []
        self._written = written

    def export(self, objs: list[object], path: str) -> None:
        self.calls.append(objs)
        with open(path, "wb") as fh:
            fh.write(self._written)


class TestExportObjectStepBytes:
    """MET-616: export must go through the object-list ``Import`` module, not a
    raw ``Shape.exportStep()`` on a flattened compound — the raw shape writer
    has no concept of FreeCAD Labels, so an assembly's children all landed in
    one anonymous PRODUCT and the multi-part structure was lost.
    """

    def test_requires_freecad(self, ops: FreecadOperations) -> None:
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.export_object_step_bytes(_leaf(_Shape()))

    def test_empty_assembly_raises_before_exporting(self, ops: FreecadOperations) -> None:
        fake_import = _FakeImport()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.Import", fake_import),
        ):
            with pytest.raises(ValueError, match="no exportable geometry"):
                ops.export_object_step_bytes(_part(label="EmptyRig"))
        assert fake_import.calls == []  # never attempted the export

    def test_exports_the_object_itself_not_a_flattened_compound(
        self, ops: FreecadOperations
    ) -> None:
        """The whole point of MET-616: pass the live object (assembly or leaf)
        straight to Import.export so it can write one PRODUCT per child,
        instead of pre-flattening to a compound that has no Label identity."""
        assembly = _part(_leaf(_Shape()), _leaf(_Shape()), label="TableAssembly")
        fake_import = _FakeImport()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.Import", fake_import),
        ):
            result = ops.export_object_step_bytes(assembly)

        assert fake_import.calls == [[assembly]]  # the container, not its leaves
        assert result == b"STEP;fake-export;MANIFOLD_SOLID_BREP"

    def test_single_leaf_object_also_goes_through_import_export(
        self, ops: FreecadOperations
    ) -> None:
        leaf = _leaf(_Shape())
        fake_import = _FakeImport()
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.Import", fake_import),
        ):
            ops.export_object_step_bytes(leaf)

        assert fake_import.calls == [[leaf]]

    def test_written_step_with_no_solid_geometry_raises(self, ops: FreecadOperations) -> None:
        """MET-652: live-caught a case where the source object's .Shape
        looked valid (so the pre-export _shape_leaves check passed), but
        Import.export wrote a STEP with only placement/context boilerplate --
        a dangling SHAPE_REPRESENTATION referencing entities that were never
        defined, no MANIFOLD_SOLID_BREP anywhere. That file was accepted as a
        "successful" export and later broke every attempt to view it. The
        written bytes must be checked too, not just the source object."""
        leaf = _leaf(_Shape())
        fake_import = _FakeImport(
            written=b"ISO-10303-21;HEADER;...no solids here...END-ISO-10303-21;"
        )
        with (
            patch("tool_registry.tools.freecad.operations.HAS_FREECAD", True),
            patch("tool_registry.tools.freecad.operations.Import", fake_import),
        ):
            with pytest.raises(ValueError, match="no exportable geometry"):
                ops.export_object_step_bytes(leaf)
