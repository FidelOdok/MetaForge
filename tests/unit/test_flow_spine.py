"""The shared spine (composable flows, story 2).

Every template restated intent, needs and feasibility word for word. They now
come from one spine, and the golden snapshot in ``test_task_contract.py``
proves the rebuilt templates compile to exactly what they were.
"""

from __future__ import annotations

import pytest
import yaml

from orchestrator.design_flow.templates import (
    TEMPLATE_DIR,
    expand_spine,
    load_spine,
    load_templates,
)


def _template(**raw: object) -> dict:
    return {"id": "t", "version": "1.0.0", "name": "T", **raw}


def test_the_spine_holds_the_shared_stages() -> None:
    spine = load_spine("spine_v1")
    assert list(spine.phases) == [
        "intent",
        "needs",
        "requirements",
        "feasibility",
        "architecture",
        "concept_selection",
        "design",
        "simulation",
        "manufacturing",
    ]
    # Every stage is gated: the spine is where people approve work.
    assert all(p.get("gate") for p in spine.phases.values())


@pytest.mark.parametrize("flow_id", ["design_v1", "mech_v1", "hardware_v1"])
def test_every_shipping_template_extends_the_spine(flow_id: str) -> None:
    raw = yaml.safe_load((TEMPLATE_DIR / f"{flow_id}.yaml").read_text())
    assert raw["extends"] == "spine_v1"
    used = [p["use"] for p in raw["phases"] if "use" in p]
    # Intent and needs are never restated.
    assert {"intent", "needs"} <= set(used)
    assert load_templates()[flow_id].spine == "spine_v1"
    assert load_templates()[flow_id].spine_version == load_spine("spine_v1").version


def test_use_pulls_the_spine_phase() -> None:
    out = expand_spine(_template(extends="spine_v1", phases=[{"use": "intent"}]))
    assert out["phases"] == [load_spine("spine_v1").phases["intent"]]


def test_keys_beside_use_override_whole_keys() -> None:
    out = expand_spine(
        _template(
            extends="spine_v1",
            phases=[{"use": "requirements", "required_deliverables": ["constraint_set"]}],
        )
    )
    (phase,) = out["phases"]
    assert phase["required_deliverables"] == ["constraint_set"]
    assert phase["objective"] == load_spine("spine_v1").phases["requirements"]["objective"]


def test_a_null_override_removes_the_key() -> None:
    out = expand_spine(_template(extends="spine_v1", phases=[{"use": "intent", "gate": None}]))
    assert "gate" not in out["phases"][0]


def test_overrides_do_not_leak_into_the_spine() -> None:
    expand_spine(_template(extends="spine_v1", phases=[{"use": "intent", "title": "Changed"}]))
    assert load_spine("spine_v1").phases["intent"]["title"] == "Intent"


def test_an_unknown_stage_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown spine phase 'nope'"):
        expand_spine(_template(extends="spine_v1", phases=[{"use": "nope"}]))


def test_use_without_extends_is_refused() -> None:
    with pytest.raises(ValueError, match="extends no spine"):
        expand_spine(_template(phases=[{"use": "intent"}]))


def test_a_used_phase_keeps_its_id() -> None:
    with pytest.raises(ValueError, match="keeps its id"):
        expand_spine(_template(extends="spine_v1", phases=[{"use": "intent", "id": "x"}]))


def test_a_template_without_extends_is_unchanged() -> None:
    raw = _template(phases=[{"id": "a", "title": "A", "objective": "o"}])
    assert expand_spine(raw) is raw


def test_an_unknown_spine_is_refused() -> None:
    with pytest.raises(FileNotFoundError):
        expand_spine(_template(extends="spine_v9", phases=[{"use": "intent"}]))
