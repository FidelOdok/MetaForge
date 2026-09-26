"""Pre-execution tool-argument validation (MET-569).

Before this, model-emitted arguments went straight to the handler: a missing
required field became a real invocation — an adapter container round-trip, or a
partially-applied side effect — that failed somewhere downstream. These tests
pin that the tool is *not* executed and the model gets something it can act on.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.harness.tools import ToolRegistry
from orchestrator.harness.validation import (
    ToolValidationError,
    is_validatable,
    validate_arguments,
    validation_errors,
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "count": {"type": "integer"},
        "kind": {"type": "string", "enum": ["box", "cylinder"]},
    },
    "required": ["session_id"],
}


def test_valid_arguments_produce_no_errors():
    assert validation_errors(_SCHEMA, {"session_id": "s1", "count": 3, "kind": "box"}) == []


def test_missing_required_property_is_reported():
    errors = validation_errors(_SCHEMA, {"count": 1})

    assert errors
    assert any("session_id" in e for e in errors)


def test_wrong_type_is_reported():
    errors = validation_errors(_SCHEMA, {"session_id": "s1", "count": "three"})

    assert any("count" in e for e in errors)


def test_value_outside_a_declared_enum_is_reported():
    errors = validation_errors(_SCHEMA, {"session_id": "s1", "kind": "torus"})

    assert any("kind" in e for e in errors)


def test_a_boolean_is_not_accepted_as_a_number():
    # In Python ``True`` is an ``int``; a model that sent ``true`` for a
    # numeric field still made a real mistake and must be told.
    assert validation_errors(_SCHEMA, {"session_id": "s1", "count": True})


def test_non_object_arguments_are_rejected():
    assert validation_errors(_SCHEMA, ["session_id"])


def test_the_permissive_fallback_schema_validates_nothing():
    # MCP tools whose manifest declares no schema get ``{"type": "object"}``.
    # Validating against it would add cost and imply a guarantee it can't make.
    assert is_validatable({"type": "object"}) is False
    assert validation_errors({"type": "object"}, {"anything": 1}) == []
    assert validation_errors({}, {"anything": 1}) == []


def test_a_broken_schema_does_not_make_the_tool_unusable():
    # A tool shipping an invalid schema is the tool author's bug; it must not
    # become an unusable tool. The structural checks still apply.
    broken = {"type": "object", "properties": {"a": {"type": "not-a-type"}}, "required": ["a"]}

    assert validation_errors(broken, {"a": "x"}) == []
    assert validation_errors(broken, {}) != []


def test_error_payload_tells_the_model_the_tool_did_not_run():
    with pytest.raises(ToolValidationError) as excinfo:
        validate_arguments("freecad.create_primitive", _SCHEMA, {})

    payload = excinfo.value.to_payload()
    assert payload["status"] == "error"
    assert payload["error"] == "invalid_arguments"
    assert payload["tool"] == "freecad.create_primitive"
    assert payload["validation_errors"]
    assert "NOT executed" in payload["hint"]


def test_reported_errors_are_capped():
    schema = {
        "type": "object",
        "properties": {f"p{i}": {"type": "string"} for i in range(30)},
        "required": [f"p{i}" for i in range(30)],
    }

    assert len(validation_errors(schema, {})) <= 8


class TestRegistryEnforcement:
    """The check lives in ``ToolRegistry.invoke``, so both loops get it."""

    @pytest.mark.asyncio
    async def test_a_bad_call_never_reaches_the_handler(self):
        calls: list[dict[str, Any]] = []

        async def handler(arguments: dict[str, Any]) -> str:
            calls.append(arguments)
            return "ran"

        registry = ToolRegistry()
        registry.register_native(
            "freecad_create_primitive",
            description="create a primitive",
            input_schema=_SCHEMA,
            handler=handler,
        )

        with pytest.raises(ToolValidationError, match="session_id"):
            await registry.invoke("freecad_create_primitive", {"count": 2})

        assert calls == []

    @pytest.mark.asyncio
    async def test_a_valid_call_still_runs(self):
        async def handler(arguments: dict[str, Any]) -> str:
            return "ran"

        registry = ToolRegistry()
        registry.register_native(
            "freecad_create_primitive",
            description="create a primitive",
            input_schema=_SCHEMA,
            handler=handler,
        )

        assert await registry.invoke("freecad_create_primitive", {"session_id": "s1"}) == "ran"

    @pytest.mark.asyncio
    async def test_validation_runs_before_the_gate_check(self):
        # Ordering matters for the model's sake: "your arguments were wrong" is
        # actionable, "this tool is gated" is not, and a call that is both
        # should get the fixable message.
        async def handler(arguments: dict[str, Any]) -> str:
            return "ran"

        registry = ToolRegistry()
        registry.register_native(
            "twin_record_decision",
            description="record",
            input_schema=_SCHEMA,
            handler=handler,
            required_gates=("twin_write",),
        )

        with pytest.raises(ToolValidationError):
            await registry.invoke("twin_record_decision", {}, gate_check=lambda _g: False)


# ---------------------------------------------------------------------------
# FORGE-229: discriminated-union oneOf/anyOf failures report the ONE branch
# the instance's own discriminator names, not a whole-instance dump with no
# field-level detail. Self-contained schema (not Design IR's own, which can
# evolve independently) shaped exactly like Pydantic's discriminated-union
# JSON Schema rendering: {"discriminator": {"propertyName", "mapping"},
# "oneOf": [...]}, refs resolved against the schema's own "$defs".
# ---------------------------------------------------------------------------

_SHAPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "shapes": {
            "type": "array",
            "items": {
                "discriminator": {
                    "propertyName": "kind",
                    "mapping": {
                        "circle": "#/$defs/Circle",
                        "rectangle": "#/$defs/Rectangle",
                    },
                },
                "oneOf": [{"$ref": "#/$defs/Circle"}, {"$ref": "#/$defs/Rectangle"}],
            },
        }
    },
    "required": ["shapes"],
    "$defs": {
        "Circle": {
            "type": "object",
            "properties": {"kind": {"const": "circle"}, "radius": {"type": "number"}},
            "required": ["kind", "radius"],
        },
        "Rectangle": {
            "type": "object",
            "properties": {
                "kind": {"const": "rectangle"},
                "width": {"type": "number"},
                "height": {"type": "number"},
            },
            "required": ["kind", "width", "height"],
        },
    },
}


class TestDiscriminatedUnionErrors:
    def test_missing_field_on_the_named_branch_is_reported_precisely(self):
        errors = validation_errors(_SHAPE_SCHEMA, {"shapes": [{"kind": "circle"}]})
        assert errors == ["shapes.0: 'radius' is a required property"]

    def test_multiple_missing_fields_are_all_reported(self):
        errors = validation_errors(_SHAPE_SCHEMA, {"shapes": [{"kind": "rectangle"}]})
        assert set(errors) == {
            "shapes.0: 'width' is a required property",
            "shapes.0: 'height' is a required property",
        }

    def test_unrecognized_discriminator_value_falls_back_to_the_generic_message(self):
        errors = validation_errors(_SHAPE_SCHEMA, {"shapes": [{"kind": "triangle"}]})
        assert len(errors) == 1
        assert errors[0].startswith("shapes.0:")
        assert "not valid under any of the given schemas" in errors[0]

    def test_valid_instance_on_either_branch_has_no_errors(self):
        assert validation_errors(_SHAPE_SCHEMA, {"shapes": [{"kind": "circle", "radius": 5}]}) == []
        assert (
            validation_errors(
                _SHAPE_SCHEMA, {"shapes": [{"kind": "rectangle", "width": 1, "height": 2}]}
            )
            == []
        )

    def test_a_nested_discriminated_union_resolves_to_the_real_leaf(self):
        """FORGE-227/229: Design IR's own shape -- an entity's discriminated
        'op' union, where one op variant (sketch) itself holds a discriminated
        'elements' union -- must bottom out at the true leaf mistake, not stop
        one level too shallow."""
        nested_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "discriminator": {
                            "propertyName": "op",
                            "mapping": {"sketch": "#/$defs/Sketch"},
                        },
                        "oneOf": [{"$ref": "#/$defs/Sketch"}],
                    },
                }
            },
            "required": ["entities"],
            "$defs": {
                "Sketch": {
                    "type": "object",
                    "properties": {
                        "op": {"const": "sketch"},
                        "elements": _SHAPE_SCHEMA["properties"]["shapes"],
                    },
                    "required": ["op", "elements"],
                },
                **_SHAPE_SCHEMA["$defs"],
            },
        }
        errors = validation_errors(
            nested_schema, {"entities": [{"op": "sketch", "elements": [{"kind": "circle"}]}]}
        )
        assert errors == ["entities.0.elements.0: 'radius' is a required property"]

    def test_real_design_ir_schema_sharpens_the_ticket_repro(self):
        """Direct regression for the ticket's own repro: a rectangle sketch
        element built the wrong way (Design IR wants origin/width/height) used
        to report only 'is not valid under any of the given schemas' for the
        WHOLE entity -- with no way to tell which field was wrong."""
        from domain_agents.mechanical.skills.generate_cad_ir.schema import GenerateCadIrInput

        schema = GenerateCadIrInput.model_json_schema()
        args = {
            "name": "Shoulder Yoke",
            "entities": [
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sketch_base",
                    "op": "sketch",
                    "body_ref": "body1",
                    "plane": "XY",
                    "elements": [{"type": "rectangle", "parameters": {"dx": 110, "dy": 60}}],
                },
            ],
        }
        errors = validation_errors(schema, args)
        assert any(
            e.startswith("entities.1.elements.0:") and "required property" in e for e in errors
        )
        assert not any("is not valid under any of the given schemas" in e for e in errors)
