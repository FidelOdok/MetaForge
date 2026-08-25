"""Unit tests for LLM intent translation (MET-436)."""

from __future__ import annotations

import json

from digital_twin.knowledge.intent_translator import (
    IntentConstraintSource,
    StubIntentLLM,
    build_intent_prompt,
    translate_intent,
)

# ---------- build_intent_prompt ----------


def test_prompt_includes_intent_text() -> None:
    prompt = build_intent_prompt(
        "step 12V down to 5V", known_categories=None, known_subsystems=None
    )
    assert "step 12V down to 5V" in prompt


def test_prompt_lists_known_categories() -> None:
    prompt = build_intent_prompt(
        "anything", known_categories=["buck_converter", "ldo"], known_subsystems=None
    )
    assert "buck_converter" in prompt
    assert "ldo" in prompt


def test_prompt_instructs_never_mark_unstated_as_stated() -> None:
    """The honesty requirement is enforced via prompting, not mechanically
    (translate_intent can't verify a model isn't lying about provenance) —
    assert the instruction is actually present in the prompt sent."""
    prompt = build_intent_prompt("anything", known_categories=None, known_subsystems=None)
    assert "stated" in prompt.lower()
    assert "never mark" in prompt.lower() or "critical" in prompt.lower()


# ---------- translate_intent: happy path ----------


async def test_happy_path_parses_categories_and_constraints() -> None:
    response = json.dumps(
        {
            "subsystem": None,
            "reasoning": "buck converter for voltage step-down",
            "categories": [
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [
                        {
                            "property": "v_in_max",
                            "op": ">=",
                            "value": 12,
                            "source": "stated",
                            "confidence": 1.0,
                        },
                        {
                            "property": "v_out",
                            "op": "==",
                            "value": 5,
                            "source": "stated",
                            "confidence": 1.0,
                        },
                        {
                            "property": "i_out_max",
                            "op": ">=",
                            "value": 2,
                            "source": "llm_inferred",
                            "confidence": 0.7,
                        },
                    ],
                }
            ],
        }
    )
    llm = StubIntentLLM({"step 12V down to 5V": response})
    result = await translate_intent(
        llm,
        intent_text="I need to step 12V down to 5V for a flight controller, around 2A",
        known_categories=["buck_converter"],
    )

    assert result.parse_error is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.category == "buck_converter"
    assert candidate.purchase_unit == "discrete_part"
    assert len(candidate.constraints) == 3

    stated = [c for c in candidate.constraints if c.source == IntentConstraintSource.STATED]
    inferred = [c for c in candidate.constraints if c.source == IntentConstraintSource.LLM_INFERRED]
    assert len(stated) == 2
    assert len(inferred) == 1
    assert inferred[0].property == "i_out_max"


async def test_cots_assembly_purchase_unit_parsed() -> None:
    response = json.dumps(
        {
            "subsystem": "flight_controller",
            "reasoning": None,
            "categories": [
                {
                    "category": "flight_controller",
                    "purchase_unit": "cots_assembly",
                    "role": None,
                    "confidence": 0.8,
                    "constraints": [],
                }
            ],
        }
    )
    llm = StubIntentLLM(lambda _prompt: response)
    result = await translate_intent(llm, intent_text="a flight controller for a 250mm quad")
    assert result.subsystem_hint == "flight_controller"
    assert result.candidates[0].purchase_unit == "cots_assembly"


# ---------- translate_intent: fail-open ----------


async def test_malformed_json_fails_open() -> None:
    llm = StubIntentLLM(lambda _prompt: "not json at all")
    result = await translate_intent(llm, intent_text="anything")
    assert result.candidates == ()
    assert result.parse_error is not None


async def test_provider_error_fails_open() -> None:
    def _raise(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    llm = StubIntentLLM(_raise)
    result = await translate_intent(llm, intent_text="anything")
    assert result.candidates == ()
    assert result.parse_error is not None


async def test_missing_categories_array_fails_open() -> None:
    llm = StubIntentLLM(lambda _prompt: json.dumps({"subsystem": None}))
    result = await translate_intent(llm, intent_text="anything")
    assert result.candidates == ()
    assert result.parse_error is not None


# ---------- translate_intent: unknown category handling ----------


async def test_unknown_category_dropped_not_trusted() -> None:
    response = json.dumps(
        {
            "subsystem": None,
            "reasoning": None,
            "categories": [
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [],
                },
                {
                    "category": "made_up_category_xyz",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [],
                },
            ],
        }
    )
    llm = StubIntentLLM(lambda _prompt: response)
    result = await translate_intent(
        llm, intent_text="anything", known_categories=["buck_converter", "ldo"]
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].category == "buck_converter"
    assert result.parse_error is not None
    assert "made_up_category_xyz" in result.parse_error


async def test_no_known_categories_trusts_everything() -> None:
    """Without a known_categories allowlist, nothing is dropped — the
    taxonomy check only applies when the caller opts into it."""
    response = json.dumps(
        {
            "subsystem": None,
            "reasoning": None,
            "categories": [
                {
                    "category": "anything_goes",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.5,
                    "constraints": [],
                }
            ],
        }
    )
    llm = StubIntentLLM(lambda _prompt: response)
    result = await translate_intent(llm, intent_text="anything")
    assert len(result.candidates) == 1
    assert result.parse_error is None


# ---------- constraint op validation ----------


async def test_invalid_op_dropped_from_constraints() -> None:
    response = json.dumps(
        {
            "subsystem": None,
            "reasoning": None,
            "categories": [
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [
                        {"property": "v_out", "op": "not_a_real_op", "value": 5},
                        {"property": "i_out_max", "op": ">=", "value": 2},
                    ],
                }
            ],
        }
    )
    llm = StubIntentLLM(lambda _prompt: response)
    result = await translate_intent(llm, intent_text="anything")
    assert len(result.candidates[0].constraints) == 1
    assert result.candidates[0].constraints[0].property == "i_out_max"


async def test_default_source_used_when_llm_omits_source() -> None:
    response = json.dumps(
        {
            "subsystem": None,
            "reasoning": None,
            "categories": [
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [{"property": "v_out", "op": "==", "value": 5}],
                }
            ],
        }
    )
    llm = StubIntentLLM(lambda _prompt: response)
    result = await translate_intent(llm, intent_text="anything")
    assert result.candidates[0].constraints[0].source == IntentConstraintSource.LLM_INFERRED
