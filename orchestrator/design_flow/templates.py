"""Flow templates as versioned files (FORGE-397).

The built-in flows were 650 lines of Python literals in ``spec.py``. That was
fine while they were fixed, and stops being fine the moment a flow can be
*tailored*: a generated flow (FORGE-398) and an edited one (FORGE-399) are
data, and they have to be comparable with the template they came from. You
cannot diff a proposal against a Python module, and you cannot say which
version a completed run used if the only answer is "whatever ``spec.py`` said
at the time".

So each flow lives in ``templates/<id>.yaml`` carrying an explicit
``version``. The loader turns one into the same :class:`FlowDefinition` the
rest of the system already consumes, so nothing downstream changes.

**The files were generated from the Python definitions, not retyped.**
Hand-copying 650 lines of prose objectives would have introduced differences
nobody would find for months, and the difference that matters — a dropped
``required_deliverables`` entry — turns a real gate into a decorative one.
``tests/unit/test_flow_templates.py`` holds the round-trip that proves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

from orchestrator.design_flow.spec import FlowDefinition, Gate, Phase

logger = structlog.get_logger(__name__)

__all__ = [
    "TEMPLATE_DIR",
    "LoadedTemplate",
    "load_template",
    "load_templates",
    "template_path",
    "to_mapping",
]

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class LoadedTemplate:
    """A flow plus the metadata that is about the template, not the flow."""

    definition: FlowDefinition
    version: str
    source: Path

    #: Short name for a person choosing between flows. ``name`` is the full
    #: descriptive title and reads as a paragraph in a radio list.
    #:
    #: These live in the template rather than the dashboard because that is
    #: the whole point of FORGE-395: the previous short names existed only in
    #: the hand-copied TypeScript, so serving `name` alone would have moved
    #: the drift rather than removed it.
    label: str = ""
    description: str = ""

    def display_label(self) -> str:
        return self.label or self.definition.name


def template_path(flow_id: str) -> Path:
    return TEMPLATE_DIR / f"{flow_id}.yaml"


def _gate_from(raw: dict[str, Any] | None) -> Gate | None:
    if raw is None:
        return None
    return Gate(
        name=raw["name"],
        auto_approve=bool(raw.get("auto_approve", False)),
        criteria=tuple(raw.get("criteria") or ()),
        enforce_constraints=bool(raw.get("enforce_constraints", False)),
        gate_id=raw.get("gate_id"),
    )


def _phase_from(raw: dict[str, Any]) -> Phase:
    return Phase(
        id=raw["id"],
        title=raw["title"],
        objective=raw["objective"],
        expected_artifacts=tuple(raw.get("expected_artifacts") or ()),
        required_deliverables=tuple(raw.get("required_deliverables") or ()),
        # Defaults match the dataclass. Spelled out rather than relying on
        # ``.get(k, default)`` matching by coincidence: a divergence here
        # would silently turn enforcement off, which is the failure mode this
        # whole module is trying to make impossible.
        enforce_deliverables=bool(raw.get("enforce_deliverables", True)),
        gate=_gate_from(raw.get("gate")),
        disciplines=tuple(raw.get("disciplines") or ()),
    )


def from_mapping(raw: dict[str, Any]) -> FlowDefinition:
    """Build a :class:`FlowDefinition` from a template mapping."""
    return FlowDefinition(
        id=raw["id"],
        name=raw["name"],
        phases=tuple(_phase_from(p) for p in raw.get("phases") or ()),
    )


def to_mapping(
    definition: FlowDefinition,
    *,
    version: str,
    label: str = "",
    description: str = "",
) -> dict[str, Any]:
    """The inverse of :func:`from_mapping`.

    Used to generate the template files from the Python definitions, and by
    the round-trip test. Keeping both directions here means the generator and
    the loader cannot drift apart.
    """
    phases: list[dict[str, Any]] = []
    for phase in definition.phases:
        entry: dict[str, Any] = {
            "id": phase.id,
            "title": phase.title,
            "objective": phase.objective,
        }
        if phase.expected_artifacts:
            entry["expected_artifacts"] = list(phase.expected_artifacts)
        if phase.required_deliverables:
            entry["required_deliverables"] = list(phase.required_deliverables)
        if not phase.enforce_deliverables:
            entry["enforce_deliverables"] = False
        if phase.disciplines:
            entry["disciplines"] = list(phase.disciplines)
        if phase.gate is not None:
            gate: dict[str, Any] = {"name": phase.gate.name}
            if phase.gate.auto_approve:
                gate["auto_approve"] = True
            if phase.gate.criteria:
                gate["criteria"] = list(phase.gate.criteria)
            if phase.gate.enforce_constraints:
                gate["enforce_constraints"] = True
            if phase.gate.gate_id:
                gate["gate_id"] = phase.gate.gate_id
            entry["gate"] = gate
        phases.append(entry)
    mapping: dict[str, Any] = {
        "id": definition.id,
        "version": version,
        "name": definition.name,
    }
    if label:
        mapping["label"] = label
    if description:
        mapping["description"] = description
    mapping["phases"] = phases
    return mapping


def load_template(flow_id: str) -> LoadedTemplate:
    """Read one template file."""
    path = template_path(flow_id)
    if not path.exists():
        raise FileNotFoundError(f"no flow template at {path}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a mapping")
    version = str(raw.get("version") or "").strip()
    if not version:
        # A template with no version is a template you cannot say a run used.
        # Refusing here is cheaper than discovering it on a run six weeks old.
        raise ValueError(f"{path} has no 'version'")
    if raw.get("id") != flow_id:
        raise ValueError(f"{path} declares id {raw.get('id')!r}, expected {flow_id!r}")
    return LoadedTemplate(
        definition=from_mapping(raw),
        version=version,
        source=path,
        label=str(raw.get("label") or "").strip(),
        description=str(raw.get("description") or "").strip(),
    )


@lru_cache(maxsize=1)
def load_templates() -> dict[str, LoadedTemplate]:
    """Every template on disk, by flow id."""
    loaded: dict[str, LoadedTemplate] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.yaml")):
        template = load_template(path.stem)
        loaded[template.definition.id] = template
    logger.info("flow_templates_loaded", count=len(loaded), ids=sorted(loaded))
    return loaded
