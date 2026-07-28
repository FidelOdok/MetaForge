"""Session-aware boolean op (MET-10).

The real cut/fuse/common runs in the FreeCAD adapter container; here we cover the
op-selection logic with fake shapes and the graceful degradation without FreeCAD.
"""

from __future__ import annotations

import pytest

from tool_registry.tools.freecad import operations as ops_mod
from tool_registry.tools.freecad.operations import FreecadNotAvailableError, FreecadOperations


class _FakeShape:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def cut(self, other: _FakeShape) -> _FakeShape:
        return _FakeShape(f"{self.tag}-cut-{other.tag}")

    def fuse(self, other: _FakeShape) -> _FakeShape:
        return _FakeShape(f"{self.tag}-fuse-{other.tag}")

    def common(self, other: _FakeShape) -> _FakeShape:
        return _FakeShape(f"{self.tag}-common-{other.tag}")


class _FakeObj:
    def __init__(self, tag: str) -> None:
        self.Shape = _FakeShape(tag)


class _FakeFeature:
    def __init__(self) -> None:
        self.Shape: _FakeShape | None = None


class _FakeDoc:
    def addObject(self, type_id: str, name: str) -> _FakeFeature:
        return _FakeFeature()

    def recompute(self) -> None:
        pass


@pytest.mark.skipif(not ops_mod.HAS_FREECAD, reason="op-logic path only runs with FreeCAD present")
@pytest.mark.parametrize(
    "operation,expect",
    [("subtract", "a-cut-b"), ("union", "a-fuse-b"), ("intersect", "a-common-b")],
)
def test_boolean_session_selects_operation(operation: str, expect: str) -> None:
    feat = FreecadOperations().boolean_session(_FakeDoc(), _FakeObj("a"), _FakeObj("b"), operation)
    assert feat.Shape.tag == expect


@pytest.mark.skipif(ops_mod.HAS_FREECAD, reason="degradation path only without FreeCAD")
def test_boolean_session_degrades_without_freecad() -> None:
    with pytest.raises(FreecadNotAvailableError):
        FreecadOperations().boolean_session(_FakeDoc(), _FakeObj("a"), _FakeObj("b"), "subtract")


def test_unsupported_operation_rejected() -> None:
    # Reaches the op check only with FreeCAD; without it, degrades first — either
    # way it never silently succeeds on a bad op.
    with pytest.raises((ValueError, FreecadNotAvailableError)):
        FreecadOperations().boolean_session(_FakeDoc(), _FakeObj("a"), _FakeObj("b"), "nonsense")
