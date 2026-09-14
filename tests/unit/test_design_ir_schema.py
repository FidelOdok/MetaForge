"""Unit tests for the Design IR schema (twin_core/design_ir/models.py, tiers.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from twin_core.design_ir import (
    OP_TIER,
    BooleanEntity,
    CreateBodyEntity,
    CreatePrimitiveEntity,
    DesignIR,
    JointEntity,
    PadEntity,
    SketchEntity,
    Tier,
    tier_for,
)


class TestEntityConstruction:
    def test_sketch_entity_with_elements(self):
        sketch = SketchEntity(
            id="sk1",
            body_ref="body1",
            plane="XY",
            elements=[
                {"type": "rectangle", "origin": (0.0, 0.0), "width": 40.0, "height": 20.0},
                {"type": "circle", "center": (5.0, 5.0), "radius": 2.0},
            ],
        )
        assert sketch.op == "sketch"
        assert len(sketch.elements) == 2
        assert sketch.elements[0].type == "rectangle"
        assert sketch.elements[1].type == "circle"

    def test_pad_requires_positive_depth(self):
        with pytest.raises(ValidationError):
            PadEntity(id="sol1", body_ref="body1", sketch_ref="sk1", depth=-5.0)

    def test_pad_rejects_zero_depth(self):
        with pytest.raises(ValidationError):
            PadEntity(id="sol1", body_ref="body1", sketch_ref="sk1", depth=0.0)

    def test_create_primitive_kind_enum(self):
        with pytest.raises(ValidationError):
            CreatePrimitiveEntity(id="p1", kind="dodecahedron")  # type: ignore[arg-type]

    def test_joint_entity_has_own_id_like_any_other_entity(self):
        """Resolves the Fusion-Gallery gap: a joint is addressable by its own id,
        not embedded as a field inside a body-pair record."""
        joint = JointEntity(
            id="j1",
            assembly_ref="asm1",
            part_a_ref="p1",
            part_b_ref="p2",
            joint_type="revolute",
        )
        assert joint.id == "j1"
        assert joint.op == "joint"

    def test_boolean_requires_at_least_one_tool_ref(self):
        with pytest.raises(ValidationError):
            BooleanEntity(id="b1", operation="union", base_ref="a", tool_refs=[])


class TestDesignIRDocument:
    def test_discriminated_union_dispatches_by_op(self):
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "sk1", "op": "sketch", "body_ref": "body1"},
                {
                    "id": "sol1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "depth": 10.0,
                },
            ]
        )
        assert isinstance(doc.entities[0], CreateBodyEntity)
        assert isinstance(doc.entities[2], PadEntity)

    def test_unknown_op_rejected(self):
        with pytest.raises(ValidationError):
            DesignIR(entities=[{"id": "x1", "op": "teleport"}])

    def test_default_units_and_schema_version(self):
        doc = DesignIR()
        assert doc.units == "mm"
        assert doc.schema_version == "0.1.0"
        assert doc.entities == []


class TestTiers:
    def test_every_entity_op_has_a_tier(self):
        """If a new entity type is added to models.py without a tiers.py entry,
        this catches the drift immediately rather than failing silently at
        routing time."""
        from typing import get_args

        from twin_core.design_ir.models import IREntity

        entity_types = get_args(get_args(IREntity)[0])
        ops = {entity_type.model_fields["op"].default for entity_type in entity_types}
        assert ops == set(OP_TIER.keys())

    def test_feature_tree_ops_are_freecad_only_tier(self):
        assert tier_for("pad") == Tier.FEATURE_TREE
        assert tier_for("fillet_edges") == Tier.FEATURE_TREE

    def test_plain_part_fillet_is_boolean_tier_not_feature_tree(self):
        """Plain Part fillet (target_ref, no body) is a different mechanism from
        PartDesign fillet_edges (body_ref, optional edge selectors) -- kept as a
        separate op per the real adapter signatures, not force-unified."""
        assert tier_for("fillet") == Tier.BOOLEAN
        assert tier_for("fillet_edges") == Tier.FEATURE_TREE

    def test_unknown_op_raises(self):
        with pytest.raises(KeyError):
            tier_for("not_a_real_op")
