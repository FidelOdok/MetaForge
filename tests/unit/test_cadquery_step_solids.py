"""A STEP written by the CadQuery adapter keeps its solids as solids.

``cq.Workplane("XY").box(10, 10, 10).faces(">Z").shell(-1)`` hands back a
``Compound`` object that wraps a valid solid. ``cq.exporters.export`` flattens
a Workplane by iterating it, which on that object yields the solid's shell, so
the STEP read back with no solid and ``cadquery.validate_shape`` failed a
perfectly good hollow enclosure. ``validity.write_step`` walks compounds on the
OCCT shape and writes each leaf as what it really is.

The stubbed tests run everywhere; ``TestRealKernel`` needs ``cadquery`` and is
skipped without it, as in ``tests/unit/test_exact_cad.py``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.cadquery import operations as operations_module
from tool_registry.tools.cadquery import validity as validity_module
from tool_registry.tools.cadquery.validity import flatten_compounds

# -- without a kernel -------------------------------------------------------


class _Node:
    def __init__(self, kind: str, *children: _Node) -> None:
        self.kind = kind
        self.children = list(children)

    def ShapeType(self) -> str:  # noqa: N802 - mirrors the OCCT name
        return self.kind

    def IsNull(self) -> bool:  # noqa: N802 - mirrors the OCCT name
        return False


def _tree() -> tuple[Any, Any]:
    return (lambda n: n.kind == "compound"), (lambda n: n.children)


def test_flatten_unwraps_nested_compounds_to_their_solids() -> None:
    a, b = _Node("solid"), _Node("solid")
    root = _Node("compound", _Node("compound", a), b)
    is_compound, children = _tree()
    assert flatten_compounds([root], is_compound=is_compound, children=children) == [a, b]


def test_flatten_keeps_an_open_shell_and_a_face_as_they_are() -> None:
    shell, face = _Node("shell"), _Node("face")
    is_compound, children = _tree()
    out = flatten_compounds(
        [_Node("compound", shell), face], is_compound=is_compound, children=children
    )
    assert out == [shell, face]


class _Shape:
    def __init__(self, wrapped: _Node) -> None:
        self.wrapped = wrapped
        self.exported: str | None = None

    @staticmethod
    def cast(node: _Node) -> _Shape:
        return _Shape(node)

    def exportStep(self, path: str) -> None:  # noqa: N802 - mirrors CadQuery
        self.exported = path

    def Solids(self) -> list[Any]:  # noqa: N802 - mirrors CadQuery
        if self.wrapped.kind == "solid":
            return [self]
        return [c for c in self.wrapped.children if c.kind == "solid"]


class _Compound(_Shape):
    made: list[_Compound] = []

    @classmethod
    def makeCompound(cls, shapes: list[_Shape]) -> _Compound:  # noqa: N802
        out = cls(_Node("compound", *[s.wrapped for s in shapes]))
        cls.made.append(out)
        return out


class _Workplane:
    def __init__(self, *vals: _Shape) -> None:
        self._vals = list(vals)

    def vals(self) -> list[_Shape]:
        return self._vals


class _StubCq:
    Workplane = _Workplane
    Shape = _Shape
    Compound = _Compound

    class exporters:  # noqa: N801 - mirrors CadQuery
        @staticmethod
        def export(*_a: Any, **_k: Any) -> None:
            raise AssertionError("cq.exporters.export must not flatten a solid result")


def test_write_step_unwraps_a_compound_wrapped_solid_before_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(validity_module, "_occt_tree", _tree)
    solid = _Node("solid")
    # the shelled-box case: a Compound object whose content is one solid
    wp = _Workplane(_Shape(_Node("compound", solid)))
    shape = validity_module.step_shape(_StubCq, wp)
    assert shape.wrapped is solid
    assert validity_module.write_step(_StubCq, wp, str(tmp_path / "a.step")) == 1


def test_write_step_writes_several_solids_as_a_compound_of_solids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(validity_module, "_occt_tree", _tree)
    _Compound.made.clear()
    wp = _Workplane(_Shape(_Node("compound", _Node("solid"), _Node("compound", _Node("solid")))))
    assert validity_module.write_step(_StubCq, wp, str(tmp_path / "b.step")) == 2
    (written,) = _Compound.made
    assert [c.kind for c in written.wrapped.children] == ["solid", "solid"]


def test_write_step_falls_back_to_the_exporter_for_an_unknown_object(tmp_path: Path) -> None:
    calls: list[str] = []

    class _BareCq:
        class exporters:  # noqa: N801 - mirrors CadQuery
            @staticmethod
            def export(_obj: Any, path: str) -> None:
                calls.append(path)

    assert validity_module.write_step(_BareCq, object(), str(tmp_path / "c.step")) == 0
    assert calls == [str(tmp_path / "c.step")]


def test_the_adapter_routes_step_through_write_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(validity_module, "write_step", lambda _cq, _o, p: seen.append(p) or 1)
    monkeypatch.setattr(operations_module, "cq", _StubCq)
    # The stub writer writes nothing; the written-file check is tested on its own.
    monkeypatch.setattr(operations_module, "require_written", lambda _p, _t: 1)
    operations_module._export_shape(object(), "/w/out.step")  # noqa: SLF001
    operations_module._export_shape(object(), "/w/out.STP")  # noqa: SLF001
    assert seen == ["/w/out.step", "/w/out.STP"]


# -- the real kernel, when this environment has one -------------------------


class TestRealKernel:
    """The adapter's STEP path against OCCT."""

    @pytest.fixture(autouse=True)
    def _kernel(self) -> None:
        pytest.importorskip("cadquery")

    @staticmethod
    def _step_of(obj: Any, tmp_path: Path) -> str:
        import cadquery as cq

        path = tmp_path / "out.step"
        validity_module.write_step(cq, obj, str(path))
        return base64.b64encode(path.read_bytes()).decode()

    @staticmethod
    def _validate(step_b64: str, tmp_path: Path, **kw: Any) -> dict[str, Any]:
        return validity_module.validate_step(step_b64, work_dir=str(tmp_path), **kw)

    def test_a_shelled_box_round_trips_as_one_solid_and_passes(self, tmp_path: Path) -> None:
        import cadquery as cq

        wp = cq.Workplane("XY").box(10, 10, 10).faces(">Z").shell(-1)
        step = self._step_of(wp, tmp_path)
        back = cq.importers.importStep(str(tmp_path / "out.step"))
        assert sum(len(v.Solids()) for v in back.vals()) == 1
        out = self._validate(step, tmp_path, expect_single_solid=True)
        assert out["outcome"] == "pass", out["findings"]
        assert out["solid_count"] == 1
        assert out["volume_mm3"] == pytest.approx(424.0, rel=1e-6)

    def test_the_adapter_export_of_a_shelled_box_passes(self, tmp_path: Path) -> None:
        import cadquery as cq

        path = tmp_path / "adapter.step"
        operations_module._export_shape(  # noqa: SLF001
            cq.Workplane("XY").box(10, 10, 10).faces(">Z").shell(-1), str(path)
        )
        out = self._validate(base64.b64encode(path.read_bytes()).decode(), tmp_path)
        assert out["outcome"] == "pass", out["findings"]

    def test_a_filleted_shelled_enclosure_passes(self, tmp_path: Path) -> None:
        import cadquery as cq

        wp = cq.Workplane("XY").box(60, 40, 25).edges("|Z").fillet(4).faces(">Z").shell(-2)
        out = self._validate(self._step_of(wp, tmp_path), tmp_path)
        assert out["outcome"] == "pass", out["findings"]
        assert out["solid_count"] == 1

    def test_two_separate_solids_pass_or_warn_when_one_is_expected(self, tmp_path: Path) -> None:
        import cadquery as cq

        wp = cq.Workplane("XY").pushPoints([(0, 0), (30, 0)]).box(10, 10, 10)
        step = self._step_of(wp, tmp_path)
        out = self._validate(step, tmp_path)
        assert out["outcome"] == "pass", out["findings"]
        assert out["solid_count"] == 2
        assert self._validate(step, tmp_path, expect_single_solid=True)["outcome"] == "warn"

    def test_a_bare_face_still_fails(self, tmp_path: Path) -> None:
        import cadquery as cq

        face = cq.Workplane("XY").box(10, 10, 10).faces(">Z").val()
        out = self._validate(self._step_of(face, tmp_path), tmp_path)
        assert out["outcome"] == "fail"
        assert out["solid_count"] == 0

    def test_an_open_shell_still_fails(self, tmp_path: Path) -> None:
        import cadquery as cq

        faces = cq.Workplane("XY").box(10, 10, 10).val().Faces()[:-1]
        shell = cq.Workplane().add(cq.Shell.makeShell(faces))
        out = self._validate(self._step_of(shell, tmp_path), tmp_path)
        assert out["outcome"] == "fail"

    def test_export_step_of_a_shelled_box_round_trips(self, tmp_path: Path) -> None:
        import cadquery as cq

        wp = cq.Workplane("XY").box(10, 10, 10).faces(">Z").shell(-1)
        out = validity_module.export_step(self._step_of(wp, tmp_path), work_dir=str(tmp_path))
        assert out["source"]["solid_count"] == 1
        back = self._validate(out["step_base64"], tmp_path)
        assert back["outcome"] == "pass" and back["solid_count"] == 1
