"""Assembly export resolution (MET-10).

`export_model` on an `App::Part` assembly used to crash with
`'App.Part' object has no attribute 'Shape'` — the container has no `.Shape` of
its own. `_shape_leaves` flattens a container into its child leaf shapes so the
export/measure path can build a compound. FreeCAD isn't in CI, so these exercise
the pure traversal with duck-typed fakes (no FreeCAD, no `Part`).
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
