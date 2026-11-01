"""FORGE-511: G6 geometry check for multi-part designs."""

from __future__ import annotations

from api_gateway.runs.geometry_constraints import check_assembly


def _box(x0: float, x1: float) -> dict:
    return {"min": [x0, 0, 0], "max": [x1, 10, 10]}


def _part(node_id: str, name: str) -> tuple[str, str, dict]:
    return (node_id, name, {"bbox_mm": _box(0, 10)})


def _assembly(parts: list[dict], bbox: dict | None) -> tuple[str, str, dict]:
    meta: dict = {"parts": parts}
    if bbox is not None:
        meta["bbox_mm"] = bbox
    return ("asm", "Shelf Assembly", meta)


def test_single_part_needs_no_assembly() -> None:
    assert check_assembly([_part("a", "A")]).violations == []


def test_multiple_parts_without_assembly_is_a_finding() -> None:
    out = check_assembly([_part("a", "A"), _part("b", "B")])
    assert any("multi-part design has no assembly" in v for v in out.violations)


def test_assembly_with_disjoint_parts_passes() -> None:
    refs = [
        {"node_id": "a", "name": "A", "position_bbox_mm": _box(0, 10)},
        {"node_id": "b", "name": "B", "position_bbox_mm": _box(10, 20)},  # touching
    ]
    out = check_assembly([_part("a", "A"), _part("b", "B"), _assembly(refs, _box(0, 20))])
    assert out.violations == []


def test_overlapping_parts_are_a_finding() -> None:
    refs = [
        {"node_id": "a", "name": "A", "position_bbox_mm": _box(0, 10)},
        {"node_id": "b", "name": "B", "position_bbox_mm": _box(5, 15)},
    ]
    out = check_assembly([_part("a", "A"), _part("b", "B"), _assembly(refs, _box(0, 15))])
    assert any("overlap" in v for v in out.violations)


def test_assembly_bbox_must_enclose_parts() -> None:
    refs = [
        {"node_id": "a", "name": "A", "position_bbox_mm": _box(0, 10)},
        {"node_id": "b", "name": "B", "position_bbox_mm": _box(10, 20)},
    ]
    out = check_assembly([_part("a", "A"), _part("b", "B"), _assembly(refs, _box(0, 12))])
    assert any("does not enclose" in v for v in out.violations)


def test_assembly_missing_a_part_is_a_finding() -> None:
    refs = [{"node_id": "a", "name": "A"}]
    out = check_assembly([_part("a", "A"), _part("b", "B"), _assembly(refs, None)])
    assert any("B" in v and "no assembly" in v for v in out.violations)


def test_assembly_parts_read_from_commit_parameters() -> None:
    """An assembly committed with its part list in ``parameters`` is still an assembly.

    The shelf assembly (2026-10-03) was committed before the ``parts`` argument
    existed, so its list sits under ``geometry_features.parameters.parts``.
    """
    from api_gateway.runs.geometry_constraints import assembly_parts

    nested = {"geometry_features": {"parameters": {"parts": [{"node_id": "a", "name": "A"}]}}}
    assert assembly_parts(nested) == [{"node_id": "a", "name": "A"}]
    assert assembly_parts({"parts": [{"node_id": "b"}]}) == [{"node_id": "b"}]
    assert assembly_parts({"geometry_features": {"parameters": {"material": "PETG"}}}) == []
