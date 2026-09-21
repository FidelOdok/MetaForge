"""Unit tests for the Patch/PatchOperation models (FORGE-50)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from twin_core.models.patch import Patch, PatchOp, PatchOperation


class TestPatchOperationValidation:
    def test_add_requires_entity_kind_and_entity(self):
        with pytest.raises(ValidationError, match="entity_kind and entity"):
            PatchOperation(op=PatchOp.ADD)

    def test_add_accepts_kind_and_entity(self):
        op = PatchOperation(
            op=PatchOp.ADD,
            entity_kind="constraint",
            entity={
                "name": "x",
                "expression": "true",
                "severity": "error",
                "domain": "mech",
                "source": "test",
            },
        )
        assert op.op == PatchOp.ADD

    def test_revise_requires_fields(self):
        with pytest.raises(ValidationError, match="entity_kind, entity_id, fields"):
            PatchOperation(op=PatchOp.REVISE, entity_kind="constraint", entity_id=uuid4())

    def test_revise_accepts_expected_revision(self):
        op = PatchOperation(
            op=PatchOp.REVISE,
            entity_kind="constraint",
            entity_id=uuid4(),
            fields={"message": "updated"},
            expected_revision=1,
        )
        assert op.expected_revision == 1

    def test_link_requires_entity_relation_target(self):
        with pytest.raises(ValidationError, match="entity_id, relation, target_id"):
            PatchOperation(op=PatchOp.LINK, entity_id=uuid4())

    def test_unlink_requires_entity_relation_target(self):
        with pytest.raises(ValidationError, match="entity_id, relation, target_id"):
            PatchOperation(op=PatchOp.UNLINK, relation="depends_on")

    def test_supersede_requires_kind_entity_target(self):
        with pytest.raises(ValidationError, match="entity_kind, entity_id, target_id"):
            PatchOperation(op=PatchOp.SUPERSEDE, entity_id=uuid4())

    def test_deprecate_requires_kind_and_entity(self):
        with pytest.raises(ValidationError, match="entity_kind, entity_id"):
            PatchOperation(op=PatchOp.DEPRECATE)

    def test_invalidate_requires_kind_and_entity(self):
        with pytest.raises(ValidationError, match="entity_kind, entity_id"):
            PatchOperation(op=PatchOp.INVALIDATE)


class TestPatchValidation:
    def test_requires_at_least_one_operation(self):
        with pytest.raises(ValidationError, match="at least one operation"):
            Patch(operations=[], reason="because")

    def test_requires_non_empty_reason(self):
        op = PatchOperation(op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=uuid4())
        with pytest.raises(ValidationError, match="reason is required"):
            Patch(operations=[op], reason="   ")

    def test_valid_patch_constructs(self):
        op = PatchOperation(op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=uuid4())
        patch = Patch(operations=[op], reason="Clarification supplied by user.")
        assert patch.reason == "Clarification supplied by user."
        assert len(patch.operations) == 1
