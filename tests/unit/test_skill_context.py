"""Skill → procedural context loader (the SKILL.md select pillar) — MET-10."""

from __future__ import annotations

from skill_registry.skill_context import (
    cards_for_domains,
    load_skill_cards,
    procedural_overlay,
    tool_ids_for_domains,
)

CARDS = load_skill_cards()


def test_loads_the_skill_corpus() -> None:
    assert len(CARDS) >= 21
    assert all(c.name and c.skill_md for c in CARDS), "every card has a name + a SKILL.md doc"
    # Domains are set for the real skills (one hollow placeholder,
    # electronics/check_power_budget, has an empty definition.json).
    assert sum(1 for c in CARDS if c.domain) >= 20


def test_cards_for_domains_filters() -> None:
    mech = cards_for_domains(CARDS, ("mechanical",))
    assert mech and all(c.domain == "mechanical" for c in mech)
    names = {c.name for c in mech}
    assert {"validate_stress", "generate_cad"} <= names


def test_overlay_carries_procedures_and_respects_budget() -> None:
    mech = cards_for_domains(CARDS, ("mechanical",))
    overlay = procedural_overlay(mech)
    assert "Reference procedures" in overlay
    assert "validate_stress" in overlay  # a real skill's SKILL.md is inlined
    # Budget is enforced (mechanical has 8 skills; a tiny budget trims + notes it).
    tiny = procedural_overlay(mech, budget_chars=400)
    assert len(tiny) <= 900  # header + one block + omission note, not all 7
    assert "omitted for context budget" in tiny


def test_empty_disciplines_give_empty_overlay() -> None:
    assert procedural_overlay(cards_for_domains(CARDS, ())) == ""


def test_tool_ids_for_domains() -> None:
    sim = tool_ids_for_domains(CARDS, ("simulation",))
    assert "calculix.run_fea" in sim
    # deduplicated
    assert len(sim) == len(set(sim))


def test_phases_declare_disciplines() -> None:
    from orchestrator.design_flow.spec import get_flow

    hw = get_flow("hardware_v1")
    by_id = {p.id: p for p in hw.phases}
    assert by_id["design"].disciplines == ("mechanical",)
    assert by_id["electronics"].disciplines == ("electronics",)
    assert by_id["firmware"].disciplines == ("firmware",)
    assert by_id["simulation"].disciplines == ("simulation",)

    # The goal-driven mechanical vertical: 3 phases, native brain, mechanical +
    # simulation procedures injected.
    mech = get_flow("mech_v1")
    assert [p.id for p in mech.phases] == ["requirements", "design", "simulation"]
    mby = {p.id: p for p in mech.phases}
    assert mby["design"].disciplines == ("mechanical",)
    assert mby["simulation"].disciplines == ("simulation",)
