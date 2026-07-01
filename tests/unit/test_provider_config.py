"""Unit tests for the declarative provider config loader (MET-547, Phase 1)."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import (
    ConfigError,
    ProviderPipeline,
    load_provider_config,
)

MINIMAL = {
    "roles": {
        "generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}],
    }
}

FULL = {
    "retry": {"api_max_retries": 4, "backoff_base_seconds": 0.25, "retryable_statuses": [429, 503]},
    "profiles": [
        {"name": "anthropic-a", "api_key_env": "ANTHROPIC_API_KEY_A"},
        {"name": "anthropic-b", "api_key_env": "ANTHROPIC_API_KEY_B", "org_id": "org-x"},
    ],
    "roles": {
        "generator": [
            {"provider": "anthropic", "model": "claude-opus-4-8"},
            {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY", "weight": 2},
        ],
        "evaluator": [{"provider": "openai", "model": "gpt-5"}],
    },
}


def test_minimal_config_defaults() -> None:
    cfg = load_provider_config(MINIMAL)
    assert cfg.retry.api_max_retries == 2  # default
    assert cfg.rotor is None
    assert [s.name for s in cfg.slots.candidates("generator")] == ["anthropic"]


def test_full_config_parses_all_sections() -> None:
    cfg = load_provider_config(FULL)
    assert cfg.retry.api_max_retries == 4
    assert cfg.retry.backoff_base_seconds == 0.25
    assert cfg.retry.retryable_statuses == frozenset({429, 503})

    gen = cfg.slots.candidates("generator")
    assert [s.name for s in gen] == ["anthropic", "openai"]
    assert gen[1].api_key_env == "OPENAI_API_KEY"
    assert gen[1].weight == 2

    assert cfg.rotor is not None
    assert cfg.rotor.pin("s1").name == "anthropic-a"  # round-robin start


def test_config_drives_the_pipeline() -> None:
    """The loaded slots resolve correctly inside a ProviderPipeline."""
    cfg = load_provider_config(FULL)
    pipeline = ProviderPipeline(cfg.slots, retry_policy=cfg.retry)
    assert [s.model for s in pipeline.resolve("evaluator")] == ["gpt-5"]


def test_missing_roles_raises() -> None:
    with pytest.raises(ConfigError, match="missing required 'roles'"):
        load_provider_config({"retry": {"api_max_retries": 1}})


def test_empty_roles_raises() -> None:
    with pytest.raises(ConfigError, match="at least one role"):
        load_provider_config({"roles": {}})


def test_empty_role_candidate_list_raises() -> None:
    with pytest.raises(ConfigError, match="at least one provider"):
        load_provider_config({"roles": {"generator": []}})


def test_spec_missing_model_raises() -> None:
    with pytest.raises(ConfigError, match=r"roles\['generator'\]\[0\] is missing a string 'model'"):
        load_provider_config({"roles": {"generator": [{"provider": "anthropic"}]}})


def test_profiles_must_be_list() -> None:
    bad = {"roles": MINIMAL["roles"], "profiles": {"name": "x"}}
    with pytest.raises(ConfigError, match="'profiles' must be a list"):
        load_provider_config(bad)


def test_profile_missing_api_key_env_raises() -> None:
    bad = {"roles": MINIMAL["roles"], "profiles": [{"name": "anthropic-a"}]}
    with pytest.raises(ConfigError, match="missing a string 'api_key_env'"):
        load_provider_config(bad)


def test_non_mapping_config_raises() -> None:
    with pytest.raises(ConfigError, match="provider config must be a mapping"):
        load_provider_config([])  # type: ignore[arg-type]
