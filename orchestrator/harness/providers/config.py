"""Declarative provider configuration (MET-547, Phase 1).

Turns a plain mapping (parsed from TOML/JSON/env, so this module stays
serialization-agnostic) into the runtime objects the harness needs:
:class:`~orchestrator.harness.providers.pipeline.RoleModelSlots`,
:class:`~orchestrator.harness.providers.pipeline.RetryPolicy`, and an optional
:class:`~orchestrator.harness.providers.rotation.ProfileRotor`.

Config shape::

    {
      "retry": {"api_max_retries": 3, "backoff_base_seconds": 0.5},
      "profiles": [
        {"name": "anthropic-a", "api_key_env": "ANTHROPIC_API_KEY_A"},
        {"name": "anthropic-b", "api_key_env": "ANTHROPIC_API_KEY_B"}
      ],
      "roles": {
        "generator": [
          {"provider": "anthropic", "model": "claude-opus-4-8"},
          {"provider": "openai",    "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"}
        ],
        "evaluator": [{"provider": "openai", "model": "gpt-5"}]
      }
    }

Only ``roles`` is required. This keeps "same loop against any provider with
zero code change" a config edit rather than a code change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from orchestrator.harness.providers.pipeline import (
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
)
from orchestrator.harness.providers.rotation import AuthProfile, ProfileRotor


class ConfigError(ValueError):
    """The provider configuration mapping was malformed."""


@dataclass(frozen=True)
class HarnessProviderConfig:
    """Runtime objects assembled from a config mapping."""

    slots: RoleModelSlots
    retry: RetryPolicy
    rotor: ProfileRotor | None


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _parse_retry(data: Mapping[str, Any]) -> RetryPolicy:
    raw = data.get("retry")
    if raw is None:
        return RetryPolicy()
    retry = _require_mapping(raw, "'retry'")
    kwargs: dict[str, Any] = {}
    if "api_max_retries" in retry:
        kwargs["api_max_retries"] = int(retry["api_max_retries"])
    if "backoff_base_seconds" in retry:
        kwargs["backoff_base_seconds"] = float(retry["backoff_base_seconds"])
    if "retryable_statuses" in retry:
        statuses = retry["retryable_statuses"]
        if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes)):
            raise ConfigError("'retry.retryable_statuses' must be a list of ints")
        kwargs["retryable_statuses"] = frozenset(int(s) for s in statuses)
    return RetryPolicy(**kwargs)


def _parse_spec(entry: Any, role: str, index: int) -> ProviderSpec:
    where = f"roles['{role}'][{index}]"
    spec = _require_mapping(entry, where)
    provider = spec.get("provider")
    model = spec.get("model")
    if not provider or not isinstance(provider, str):
        raise ConfigError(f"{where} is missing a string 'provider'")
    if not model or not isinstance(model, str):
        raise ConfigError(f"{where} is missing a string 'model'")
    extra_raw = spec.get("extra", {})
    extra = {str(k): str(v) for k, v in _require_mapping(extra_raw, f"{where}.extra").items()}
    return ProviderSpec(
        name=provider,
        model=model,
        api_key_env=spec.get("api_key_env"),
        base_url=spec.get("base_url"),
        weight=int(spec.get("weight", 1)),
        extra=extra,
    )


def _parse_roles(data: Mapping[str, Any]) -> RoleModelSlots:
    raw = data.get("roles")
    if raw is None:
        raise ConfigError("config is missing required 'roles'")
    roles = _require_mapping(raw, "'roles'")
    if not roles:
        raise ConfigError("'roles' must define at least one role")
    slots: dict[str, list[ProviderSpec]] = {}
    for role, entries in roles.items():
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            raise ConfigError(f"roles['{role}'] must be a list of provider specs")
        if not entries:
            raise ConfigError(f"roles['{role}'] must list at least one provider")
        slots[str(role)] = [_parse_spec(e, str(role), i) for i, e in enumerate(entries)]
    return RoleModelSlots(slots=slots)


def _parse_rotor(data: Mapping[str, Any]) -> ProfileRotor | None:
    raw = data.get("profiles")
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ConfigError("'profiles' must be a list of auth profiles")
    if not raw:
        raise ConfigError("'profiles', if present, must list at least one profile")
    profiles: list[AuthProfile] = []
    for i, entry in enumerate(raw):
        prof = _require_mapping(entry, f"profiles[{i}]")
        name = prof.get("name")
        api_key_env = prof.get("api_key_env")
        if not name or not isinstance(name, str):
            raise ConfigError(f"profiles[{i}] is missing a string 'name'")
        if not api_key_env or not isinstance(api_key_env, str):
            raise ConfigError(f"profiles[{i}] is missing a string 'api_key_env'")
        profiles.append(
            AuthProfile(
                name=name,
                api_key_env=api_key_env,
                org_id=prof.get("org_id"),
                base_url=prof.get("base_url"),
            )
        )
    return ProfileRotor(profiles)


def load_provider_config(data: Mapping[str, Any]) -> HarnessProviderConfig:
    """Build a :class:`HarnessProviderConfig` from a config mapping.

    Raises :class:`ConfigError` with a path-qualified message on any malformed
    field, so a bad config fails fast and legibly at startup.
    """
    data = _require_mapping(data, "provider config")
    return HarnessProviderConfig(
        slots=_parse_roles(data),
        retry=_parse_retry(data),
        rotor=_parse_rotor(data),
    )
