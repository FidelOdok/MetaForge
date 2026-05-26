"""Unit tests for ``digital_twin.memory.consolidation.llm``."""

from __future__ import annotations

import pytest

from digital_twin.memory.consolidation.llm import StubLLMClient, parse_strict_json


@pytest.mark.asyncio
async def test_stub_dispatches_on_substring():
    stub = StubLLMClient(
        responses={
            "mechanical": {"narrative": "a", "confidence": 0.8},
            "circuit": {"narrative": "b", "confidence": 0.7},
        }
    )
    a = await stub.synthesize_insight("Theme: mechanical_validation\n...")
    b = await stub.synthesize_insight("Theme: circuit_design_rule\n...")
    assert a["narrative"] == "a"
    assert b["narrative"] == "b"


@pytest.mark.asyncio
async def test_stub_list_responses_round_robin():
    stub = StubLLMClient(
        responses=[
            {"narrative": "one", "confidence": 0.8},
            {"narrative": "two", "confidence": 0.9},
        ]
    )
    first = await stub.synthesize_insight("prompt 1")
    second = await stub.synthesize_insight("prompt 2")
    third = await stub.synthesize_insight("prompt 3")
    assert first["narrative"] == "one"
    assert second["narrative"] == "two"
    assert third["narrative"] == "one"  # wraps around


@pytest.mark.asyncio
async def test_stub_default_response_when_unmatched():
    stub = StubLLMClient()
    response = await stub.synthesize_insight("any prompt")
    assert response["narrative"] == "no_response"
    assert response["confidence"] == 0.0


@pytest.mark.asyncio
async def test_stub_records_calls():
    stub = StubLLMClient()
    await stub.synthesize_insight("first")
    await stub.synthesize_insight("second")
    assert stub.calls == ["first", "second"]


def test_parse_strict_json_plain():
    assert parse_strict_json('{"a": 1}') == {"a": 1}


def test_parse_strict_json_fenced():
    fenced = '```json\n{"a": 1}\n```'
    assert parse_strict_json(fenced) == {"a": 1}


def test_parse_strict_json_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        parse_strict_json("[1, 2, 3]")


def test_stub_isolates_canned_response_mutation():
    canned = {"narrative": "x", "confidence": 0.8, "nested": {"k": "v"}}
    stub = StubLLMClient(responses=[canned])

    import asyncio

    response = asyncio.run(stub.synthesize_insight("anything"))
    response["narrative"] = "mutated"
    response["nested"]["k"] = "tampered"
    assert canned["narrative"] == "x"
    assert canned["nested"]["k"] == "v"
