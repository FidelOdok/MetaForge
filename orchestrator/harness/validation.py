"""Pre-execution validation of model-emitted tool arguments (MET-569).

Until now arguments went from the model straight to the handler: a missing
required field or a string where a number belonged became a tool *invocation*
that failed somewhere downstream — inside an adapter container, or worse,
halfway through a real side effect — and came back to the model as an opaque
message. The model burned a step, and a CAD/FEA adapter burned a container
round-trip, to learn something the declared schema already knew.

Validating first turns that class of mistake into an immediate, structured,
self-correctable answer with no invocation at all.

Deliberately narrow: only the checks a model actually gets wrong and the schema
states unambiguously — missing ``required`` properties, wrong JSON types, and
values outside a declared ``enum``. Everything else (formats, ranges, nested
object shapes) is left to the tool, which owns its own semantics. ``jsonschema``
is used when installed, so declared constraints are honoured in full; the
built-in fallback keeps the required/type/enum guarantees when it is not.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

try:  # pragma: no cover — presence depends on the environment
    from jsonschema import Draft202012Validator

    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    Draft202012Validator = None  # type: ignore[assignment,misc]
    HAS_JSONSCHEMA = False

MAX_REPORTED_ERRORS = 8
"""Cap the report so a wildly wrong call can't push a wall of text into the
model's context. The first few errors are always the actionable ones."""

# JSON Schema type name -> the Python types that satisfy it. ``bool`` is
# excluded from the numeric entries on purpose: in Python ``True`` is an
# ``int``, but a model that sends ``true`` for a numeric field made a real
# mistake and should be told.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


class ToolValidationError(ValueError):
    """Model-emitted arguments do not satisfy the tool's declared schema.

    Carries the individual failures so the loop can hand the model a
    structured result instead of a flattened string.
    """

    def __init__(self, tool: str, errors: list[str]) -> None:
        self.tool = tool
        self.errors = errors
        joined = "; ".join(errors)
        super().__init__(f"invalid arguments for '{tool}': {joined}")

    def to_payload(self) -> dict[str, Any]:
        """The structured tool result the model sees."""
        return {
            "status": "error",
            "error": "invalid_arguments",
            "tool": self.tool,
            "validation_errors": self.errors,
            "hint": (
                "Fix the arguments to match the tool's declared schema and call "
                "it again. The tool was NOT executed."
            ),
        }


def is_validatable(schema: Any) -> bool:
    """Whether ``schema`` says anything worth checking.

    The permissive ``{"type": "object"}`` fallback that MCP tools without a
    manifest schema get carries no constraints, so validating against it would
    only add overhead and false confidence.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    return bool(schema.get("required")) or bool(schema.get("properties"))


def _type_error(name: str, value: Any, declared: Any) -> str | None:
    names = declared if isinstance(declared, list) else [declared]
    allowed: tuple[type, ...] = ()
    for entry in names:
        if entry == "null":
            if value is None:
                return None
            continue
        allowed += _TYPE_MAP.get(str(entry), ())
    if not allowed:
        return None
    if isinstance(value, bool) and "boolean" not in [str(n) for n in names]:
        return f"'{name}' must be {declared}, got boolean"
    if not isinstance(value, allowed):
        return f"'{name}' must be {declared}, got {type(value).__name__}"
    return None


def _fallback_errors(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for name in schema.get("required") or []:
        if name not in arguments:
            errors.append(f"missing required property '{name}'")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if name not in arguments or not isinstance(spec, dict):
                continue
            value = arguments[name]
            declared = spec.get("type")
            if declared is not None:
                message = _type_error(name, value, declared)
                if message:
                    errors.append(message)
                    continue
            choices = spec.get("enum")
            if isinstance(choices, list) and choices and value not in choices:
                errors.append(f"'{name}' must be one of {choices}, got {value!r}")
    return errors


def _discriminator_ref(schema: Any, instance: Any) -> str | None:
    """The local ``$ref`` ``instance`` resolves to, per ``schema``'s own
    ``discriminator`` hint (``propertyName`` + ``mapping``) -- how Pydantic
    renders a discriminated ``Annotated`` union to JSON Schema. ``None`` when
    the schema has no discriminator, ``instance`` isn't an object, or its
    discriminator value has no mapping entry (an unrecognized/misspelled
    type -- there's no single branch to blame, so the generic error is the
    right one to keep in that case).
    """
    if not isinstance(schema, dict) or not isinstance(instance, dict):
        return None
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):
        return None
    prop_name = discriminator.get("propertyName")
    mapping = discriminator.get("mapping")
    if not (isinstance(prop_name, str) and isinstance(mapping, dict)):
        return None
    value = instance.get(prop_name)
    ref = mapping.get(value) if isinstance(value, str) else None
    return ref if isinstance(ref, str) else None


def _resolve_local_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a ``#/$defs/Name``-shaped ref against ``root_schema``'s own
    ``$defs``. Pydantic's generated schemas only ever use this exact local
    shape, so anything else (external refs) is deliberately left alone."""
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return None
    defs = root_schema.get("$defs")
    if not isinstance(defs, dict):
        return None
    sub = defs.get(ref[len(prefix) :])
    return sub if isinstance(sub, dict) else None


def _sharpen_oneof_error(
    validator: Draft202012Validator,
    root_schema: dict[str, Any],
    err: Any,
    path_prefix: list[Any],
) -> list[tuple[list[Any], str]]:
    """Replace a ``oneOf``/``anyOf`` failure's opaque "is not valid under any
    of the given schemas" with the errors from the ONE branch ``err``'s own
    discriminator property picks out — field-level and precise, instead of
    every branch's failure flattened into a single whole-instance dump.
    Recurses, so a discriminated union nested inside another (a Design IR
    sketch entity's own discriminated ``elements``, inside the entity's own
    discriminated ``op`` union) still bottoms out at the real leaf mistake.

    ``path_prefix`` is ``err``'s own absolute path in the ORIGINAL top-level
    document -- ``validator.descend(err.instance, ...)`` reports each
    produced error's path relative to ``err.instance``, not the document
    root, so it has to be re-prepended by hand to get a path the model can
    actually act on (``entities.1.elements.0.origin``, not just ``origin``).

    Returns ``[]`` when there's no discriminator to follow (the caller keeps
    reporting ``err`` itself, unchanged, in that case) -- this only ever
    sharpens a message, never suppresses one.
    """
    ref = _discriminator_ref(err.schema, err.instance)
    if ref is None:
        return []
    sub = _resolve_local_ref(root_schema, ref)
    if sub is None:
        return []
    leaves: list[tuple[list[Any], str]] = []
    for sub_err in validator.descend(err.instance, sub, path=None, schema_path=None):
        full_path = path_prefix + list(sub_err.absolute_path)
        if sub_err.context:
            deeper = _sharpen_oneof_error(validator, root_schema, sub_err, full_path)
            leaves.extend(deeper if deeper else [(full_path, sub_err.message)])
        else:
            leaves.append((full_path, sub_err.message))
    return leaves


def validation_errors(schema: Any, arguments: Any) -> list[str]:
    """Human-readable reasons ``arguments`` fail ``schema``; empty when valid."""
    if not is_validatable(schema):
        return []
    if not isinstance(arguments, dict):
        return [f"arguments must be an object, got {type(arguments).__name__}"]
    if HAS_JSONSCHEMA:
        try:
            validator = Draft202012Validator(schema)
            errors: list[tuple[list[Any], str]] = []
            for err in validator.iter_errors(arguments):
                # FORGE-229: a discriminated union (Design IR entities, and
                # each entity's own discriminated sketch elements) fails
                # oneOf/anyOf with the whole instance echoed back and no
                # field-level detail -- the model can't tell which of its
                # guesses was closest, let alone what to fix. When the
                # instance carries the discriminator property, descend into
                # the ONE branch it actually names instead.
                path = list(err.absolute_path)
                sharpened = (
                    _sharpen_oneof_error(validator, schema, err, path) if err.context else []
                )
                errors.extend(sharpened if sharpened else [(path, err.message)])
            found = [
                f"{'.'.join(str(p) for p in path) or 'arguments'}: {message}"
                for path, message in errors
            ]
        except Exception as exc:  # noqa: BLE001 — a broken schema is not the model's fault
            # A tool shipping an invalid schema must not become an unusable
            # tool: log it and fall through to the structural checks.
            logger.warning("tool_schema_invalid", error=str(exc))
            return _fallback_errors(schema, arguments)
        return found[:MAX_REPORTED_ERRORS]
    return _fallback_errors(schema, arguments)[:MAX_REPORTED_ERRORS]


def validate_arguments(tool: str, schema: Any, arguments: Any) -> None:
    """Raise :class:`ToolValidationError` when ``arguments`` don't fit ``schema``."""
    errors = validation_errors(schema, arguments)
    if errors:
        logger.info("tool_arguments_rejected", tool=tool, errors=errors)
        raise ToolValidationError(tool, errors)
