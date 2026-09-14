"""Unit tests for twin_core/design_ir/validation.py.

Bounds-sanity (positive dimensions, pattern counts, angle ranges) is
covered in test_design_ir_schema.py since it's enforced by Pydantic field
constraints at construction time, not by anything here. This file covers
what can only be checked against the whole document: referential
integrity and duplicate ids.
"""

from __future__ import annotations

from twin_core.design_ir import DesignIR
from twin_core.design_ir.validation import validate_design_ir, validate_referential_integrity


def _bracket_doc() -> DesignIR:
    """Matches the requirements doc's §13 Appendix A worked example (sketch -> pad -> fillet)."""
    return DesignIR(
        entities=[
            {"id": "body1", "op": "create_body"},
            {
                "id": "sk1",
                "op": "sketch",
                "body_ref": "body1",
                "plane": "XY",
                "elements": [
                    {"type": "rectangle", "origin": (0.0, 0.0), "width": 40.0, "height": 20.0}
                ],
            },
            {"id": "sol1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 10.0},
            {
                "id": "sol2",
                "op": "fillet_edges",
                "body_ref": "body1",
                "radius": 2.0,
                "edge_selectors": ["Edge3"],
            },
        ]
    )


class TestValidDocuments:
    def test_bracket_worked_example_is_valid(self):
        assert validate_design_ir(_bracket_doc()) == []

    def test_joint_between_two_earlier_parts_is_valid(self):
        doc = DesignIR(
            entities=[
                {"id": "p1", "op": "create_primitive", "kind": "box"},
                {"id": "p2", "op": "create_primitive", "kind": "cylinder"},
                {"id": "asm1", "op": "create_assembly"},
                {
                    "id": "j1",
                    "op": "joint",
                    "assembly_ref": "asm1",
                    "part_a_ref": "p1",
                    "part_b_ref": "p2",
                    "joint_type": "revolute",
                },
            ]
        )
        assert validate_referential_integrity(doc) == []

    def test_empty_edge_selectors_means_all_edges_not_a_broken_ref(self):
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "sol1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 5.0},
                {"id": "sol2", "op": "fillet_edges", "body_ref": "body1", "radius": 1.0},
            ]
        )
        # sk1 is genuinely unresolved here (not defined) -- one real error expected,
        # but the empty edge_selectors on sol2 must not itself produce a second error.
        errors = validate_referential_integrity(doc)
        assert len(errors) == 1
        assert "sketch_ref" in errors[0]


class TestReferentialIntegrityViolations:
    def test_forward_reference_rejected(self):
        """An entity may not reference one defined later in the list."""
        doc = DesignIR(
            entities=[
                {"id": "sol1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 5.0},
                {"id": "body1", "op": "create_body"},
            ]
        )
        errors = validate_referential_integrity(doc)
        assert any("body_ref" in e and "body1" in e for e in errors)

    def test_unknown_reference_rejected(self):
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sol1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "does_not_exist",
                    "depth": 5.0,
                },
            ]
        )
        errors = validate_referential_integrity(doc)
        assert len(errors) == 1
        assert "does_not_exist" in errors[0]

    def test_self_reference_rejected(self):
        """An entity is never 'earlier' than itself."""
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "sol1", "op": "fillet", "target_ref": "sol1", "radius": 1.0},
            ]
        )
        errors = validate_referential_integrity(doc)
        assert any("target_ref" in e for e in errors)

    def test_duplicate_id_rejected(self):
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "body1", "op": "create_body"},
            ]
        )
        errors = validate_referential_integrity(doc)
        assert any("duplicate id" in e for e in errors)

    def test_edge_selectors_are_never_checked_as_entity_references(self):
        """'Edge99' is a real FreeCAD topology name, not a reference to another
        entity -- confirmed against tool_registry/tools/freecad/operations.py,
        FreeCAD's own fillet_edges/chamfer_edges take plain selector strings
        like 'Edge3' scoped to body_ref's tip, not a dotted entity reference.
        A document naming an edge selector that happens to share no id with
        anything in the document must still validate cleanly."""
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "sol1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 5.0},
            ]
        )
        doc2 = DesignIR(
            entities=[
                *doc.entities,
                {
                    "id": "sol2",
                    "op": "fillet_edges",
                    "body_ref": "body1",
                    "radius": 1.0,
                    "edge_selectors": ["Edge99"],
                },
            ]
        )
        errors = validate_referential_integrity(doc2)
        assert not any("Edge99" in e for e in errors)
