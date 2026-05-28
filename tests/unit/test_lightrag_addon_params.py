"""Unit tests for LightRAG addon_params resolution (MET-466 Task 2)."""

from __future__ import annotations

from digital_twin.knowledge.lightrag_service import (
    DEFAULT_KB_ENTITY_TYPES,
    DEFAULT_KB_LANGUAGE,
    LightRAGConfig,
    resolve_kb_addon_params,
)


def test_default_kb_entity_types_match_spec():
    # MET-466 Task 2: "Configure entity types: Component, Supplier,
    # Property, Constraint".
    assert DEFAULT_KB_ENTITY_TYPES == ("Component", "Supplier", "Property", "Constraint")


def test_lightrag_config_defaults_addon_fields_to_none():
    cfg = LightRAGConfig()
    assert cfg.entity_types is None
    assert cfg.language is None


def test_resolve_addon_params_uses_canonical_defaults_when_unset():
    params = resolve_kb_addon_params(LightRAGConfig())
    assert params["entity_types"] == list(DEFAULT_KB_ENTITY_TYPES)
    assert params["language"] == DEFAULT_KB_LANGUAGE


def test_resolve_addon_params_honors_custom_entity_types():
    cfg = LightRAGConfig(entity_types=("Resistor", "Capacitor"))
    params = resolve_kb_addon_params(cfg)
    assert params["entity_types"] == ["Resistor", "Capacitor"]
    # Language still defaults when the caller picks custom types but no language.
    assert params["language"] == DEFAULT_KB_LANGUAGE


def test_resolve_addon_params_honors_custom_language():
    cfg = LightRAGConfig(language="French")
    params = resolve_kb_addon_params(cfg)
    assert params["language"] == "French"
    assert params["entity_types"] == list(DEFAULT_KB_ENTITY_TYPES)


def test_resolve_addon_params_empty_entity_types_lets_lightrag_default():
    # Passing the empty tuple is the explicit escape hatch — disables the
    # canonical override so LightRAG uses its own built-in defaults.
    cfg = LightRAGConfig(entity_types=())
    params = resolve_kb_addon_params(cfg)
    assert "entity_types" not in params
    assert "language" not in params  # nothing to pair with


def test_resolve_addon_params_empty_entity_types_keeps_language_when_set():
    cfg = LightRAGConfig(entity_types=(), language="German")
    params = resolve_kb_addon_params(cfg)
    assert "entity_types" not in params
    assert params["language"] == "German"


def test_resolve_addon_params_returned_list_is_caller_owned():
    # Returned dict must not share the canonical tuple — mutating the
    # result should not affect future calls.
    params = resolve_kb_addon_params(LightRAGConfig())
    params["entity_types"].append("Mutation")
    fresh = resolve_kb_addon_params(LightRAGConfig())
    assert "Mutation" not in fresh["entity_types"]
