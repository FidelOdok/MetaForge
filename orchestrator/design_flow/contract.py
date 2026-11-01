"""The task contract of a composable flow: inputs, outputs, checks.

A *task* is a gateless :class:`~orchestrator.design_flow.spec.Phase` a domain
fragment contributes to a flow. It declares:

* ``inputs``: the twin items it reads, as ``type:name`` references
  (``design_value:heat_load_w``, ``cad_model:enclosure``);
* outputs: the items it writes, as the same references, which become its
  deliverable slots (FORGE-524), so every write lands on a fixed item key;
* ``checks``: deterministic acceptance criteria over design values;
* ``capabilities``: the tool ids it needs.

Inputs and outputs are what composition derives dependencies from, so they
are parsed and validated here, once, for the template loader, the version
store and the fragment registry alike. Pure functions, no I/O.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported lazily at run time: ``spec`` loads the flow templates when it
    # is imported, and the template loader uses this module, so a top-level
    # import would be a cycle whenever this module is imported first.
    from orchestrator.design_flow.spec import Check, DeliverableSlot, Phase

__all__ = [
    "CHECK_OPS",
    "ContractError",
    "check_from",
    "check_to_mapping",
    "checks_from",
    "outputs_of",
    "parse_ref",
    "ref_key",
    "slots_from_outputs",
    "task_fields_from",
    "task_fields_to",
]

#: The comparison operators a check may use. Nothing else: a check decides
#: whether a gate passes, so it has to be readable by the person approving it.
CHECK_OPS = ("<=", "<", ">=", ">", "==", "!=")

_REF = re.compile(r"^(?P<type>[a-z][a-z0-9_]*):(?P<name>[A-Za-z0-9][A-Za-z0-9_ .\-]*)$")
_NAME = r"[a-z][a-z0-9_]*"
_CHECK_TEXT = re.compile(
    rf"^\s*(?P<value>{_NAME})\s*(?P<op><=|>=|==|!=|<|>)\s*"
    r"(?:(?P<threshold>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?P<unit>\S.*?)?"
    rf"|(?P<limit>{_NAME}))\s*$"
)


class ContractError(ValueError):
    """A task's inputs, outputs or checks are not well formed."""


def parse_ref(ref: str) -> tuple[str, str]:
    """``"design_value:heat_load_w"`` -> ``("design_value", "heat_load_w")``.

    The type must be a definition type in the twin's item registry: only
    definitions are revisioned items, and an input that is not a revisioned
    item cannot go stale, which is the whole point of declaring it.
    """
    match = _REF.match(str(ref or "").strip())
    if match is None:
        raise ContractError(f"'{ref}' is not a 'type:name' item reference")
    item_type, name = match.group("type"), match.group("name").strip()
    from twin_core.items.registry import is_definition

    if not is_definition(item_type):
        raise ContractError(f"'{ref}': '{item_type}' is not a versioned definition type")
    return item_type, name


def ref_key(ref: str) -> str:
    """The item key a reference resolves to (the same key its slot binds)."""
    from orchestrator.design_flow.slots import slot_key

    item_type, name = parse_ref(ref)
    return slot_key(item_type, name)


def slots_from_outputs(outputs: Iterable[str]) -> tuple[DeliverableSlot, ...]:
    """One keyed deliverable slot per output reference, duplicates refused."""
    from orchestrator.design_flow.spec import DeliverableSlot

    slots: list[DeliverableSlot] = []
    seen: set[str] = set()
    for ref in outputs:
        item_type, name = parse_ref(ref)
        key = ref_key(ref)
        if key in seen:
            raise ContractError(f"output '{ref}' is declared twice")
        seen.add(key)
        slots.append(DeliverableSlot(item_type=item_type, name=name, item_key=key))
    return tuple(slots)


def outputs_of(phase: Phase) -> tuple[str, ...]:
    """A phase's outputs as ``type:name`` references (from its slots)."""
    return tuple(f"{s.item_type}:{s.name}" for s in phase.slots)


def check_from(raw: Any) -> Check:
    """A check from text or a mapping.

    Text: ``"max_temp_c <= 85 degC"`` (a literal bound) or
    ``"max_temp_c <= max_allowed_temp_c"`` (another design value). Mapping:
    ``{id, value, op, threshold | limit, unit}``.
    """
    from orchestrator.design_flow.spec import Check

    if isinstance(raw, str):
        match = _CHECK_TEXT.match(raw)
        if match is None:
            raise ContractError(
                f"cannot read check '{raw}'; use 'value_name <op> number [unit]' or "
                f"'value_name <op> limit_name' with one of {', '.join(CHECK_OPS)}"
            )
        value = match.group("value")
        if match.group("limit"):
            return Check(id=value, value=value, op=match.group("op"), limit=match.group("limit"))
        return Check(
            id=value,
            value=value,
            op=match.group("op"),
            threshold=float(match.group("threshold")),
            unit=(match.group("unit") or "").strip(),
        )
    if not isinstance(raw, Mapping):
        raise ContractError(f"a check must be a string or a mapping, got {type(raw).__name__}")
    value = str(raw.get("value") or "").strip()
    op = str(raw.get("op") or "").strip()
    if not re.match(r"^[a-z][a-z0-9_]*$", value):
        raise ContractError(f"check {dict(raw)!r}: 'value' must name a design value")
    if op not in CHECK_OPS:
        raise ContractError(f"check '{value}': op '{op}' is not one of {', '.join(CHECK_OPS)}")
    limit = str(raw.get("limit") or "").strip()
    if limit:
        if not re.match(rf"^{_NAME}$", limit):
            raise ContractError(f"check '{value}': 'limit' must name a design value")
        if "threshold" in raw:
            raise ContractError(f"check '{value}': give a 'limit' or a 'threshold', not both")
        return Check(
            id=str(raw.get("id") or value).strip(),
            value=value,
            op=op,
            limit=limit,
            unit=str(raw.get("unit") or "").strip(),
        )
    try:
        threshold = float(raw["threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"check '{value}': 'threshold' must be a number") from exc
    if not math.isfinite(threshold):
        raise ContractError(f"check '{value}': 'threshold' must be finite")
    return Check(
        id=str(raw.get("id") or value).strip(),
        value=value,
        op=op,
        threshold=threshold,
        unit=str(raw.get("unit") or "").strip(),
    )


def checks_from(raw: Any) -> tuple[Check, ...]:
    """Parse a list of checks; ids must be unique within the task."""
    checks = tuple(check_from(entry) for entry in raw or ())
    ids = [c.id for c in checks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ContractError(f"duplicate check id(s): {', '.join(dupes)}")
    return checks


def check_to_mapping(check: Check) -> dict[str, Any]:
    out: dict[str, Any] = {"id": check.id, "value": check.value, "op": check.op}
    if check.limit:
        out["limit"] = check.limit
    else:
        out["threshold"] = check.threshold
    if check.unit:
        out["unit"] = check.unit
    return out


def task_fields_from(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The task-contract keyword arguments of a :class:`Phase` from a mapping.

    Shared by the template loader and the version store, so a task written by
    one reads back identically through the other. Inputs are validated as
    references; an empty mapping yields the defaults (a plain phase).
    """
    from orchestrator.design_flow.spec import Coupling

    inputs = tuple(str(r).strip() for r in raw.get("inputs") or ())
    for ref in inputs:
        parse_ref(ref)
    coupling_raw = raw.get("coupling")
    coupling = None
    if isinstance(coupling_raw, Mapping):
        coupling = Coupling(
            members=tuple(str(m) for m in coupling_raw.get("members") or ()),
            solves_for=tuple(str(v) for v in coupling_raw.get("solves_for") or ()),
            tolerance=float(coupling_raw.get("tolerance", 0.01)),
            max_iterations=int(coupling_raw.get("max_iterations", 20)),
        )
    return {
        "coupling": coupling,
        "fragment": str(raw.get("fragment") or ""),
        "stage": str(raw.get("stage") or ""),
        "inputs": inputs,
        "checks": checks_from(raw.get("checks")),
        "capabilities": tuple(str(c).strip() for c in raw.get("capabilities") or ()),
    }


def task_fields_to(phase: Phase) -> dict[str, Any]:
    """The inverse of :func:`task_fields_from`: only the fields that are set."""
    out: dict[str, Any] = {}
    if phase.fragment:
        out["fragment"] = phase.fragment
    if phase.stage:
        out["stage"] = phase.stage
    if phase.inputs:
        out["inputs"] = list(phase.inputs)
    if phase.checks:
        out["checks"] = [check_to_mapping(c) for c in phase.checks]
    if phase.capabilities:
        out["capabilities"] = list(phase.capabilities)
    if phase.coupling is not None:
        out["coupling"] = {
            "members": list(phase.coupling.members),
            "solves_for": list(phase.coupling.solves_for),
            "tolerance": phase.coupling.tolerance,
            "max_iterations": phase.coupling.max_iterations,
        }
    return out
