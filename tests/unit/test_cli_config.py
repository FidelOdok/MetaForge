"""Unit tests for `forge config` + the wizard (MET-555)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cli.forge_cli.config import ForgeConfig, run_wizard


class StubClient:
    """Duck-typed ForgeClient returning canned harness capability data."""

    def __init__(self) -> None:
        self.models_provider: str | None = None

    def list_harness_providers(self) -> dict[str, Any]:
        return {
            "active_provider": "openai",
            "active_model": "gpt-4o",
            "providers": [
                {"id": "anthropic", "family": "anthropic", "configured": True, "base_url": None},
                {"id": "openai", "family": "openai", "configured": True, "base_url": None},
                {"id": "ollama", "family": "openai", "configured": True, "base_url": None},
            ],
        }

    def list_harness_models(self, provider: str) -> dict[str, Any]:
        self.models_provider = provider
        if provider == "openai":
            return {
                "provider": provider,
                "models": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}],
                "source": "live",
            }
        return {"provider": provider, "models": [], "source": "none"}


def _scripted_input(answers: list[str]):
    it = iter(answers)

    def _input(_prompt: str) -> str:
        return next(it)

    return _input


def test_config_load_missing_returns_defaults(tmp_path: Path) -> None:
    cfg = ForgeConfig.load(tmp_path / "nope.json")
    assert cfg == ForgeConfig()
    assert cfg.mode == "ask"


def test_config_save_and_reload_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    ForgeConfig(gateway_url="http://gw:8000", provider="openai", model="gpt-4o", mode="plan").save(
        p
    )
    cfg = ForgeConfig.load(p)
    assert cfg.gateway_url == "http://gw:8000"
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"
    assert cfg.mode == "plan"


def test_config_load_bad_mode_falls_back_to_ask(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text('{"mode": "bogus"}', encoding="utf-8")
    assert ForgeConfig.load(p).mode == "ask"


def test_config_load_invalid_json_returns_defaults(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    assert ForgeConfig.load(p) == ForgeConfig()


def test_wizard_picks_provider_and_model_by_number() -> None:
    client = StubClient()
    # answers: gateway URL, provider (2 → openai), model (1 → gpt-4o), mode
    inp = _scripted_input(["http://gw:8000", "2", "1", "auto"])
    cfg = run_wizard(client, existing=ForgeConfig(), input_fn=inp)  # type: ignore[arg-type]
    assert cfg.gateway_url == "http://gw:8000"
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"
    assert cfg.mode == "auto"
    assert client.models_provider == "openai"  # models were fetched for the chosen provider


def test_wizard_picks_provider_and_model_by_name() -> None:
    client = StubClient()
    inp = _scripted_input(["http://gw:8000", "anthropic", "claude-x", "plan"])
    cfg = run_wizard(client, existing=ForgeConfig(), input_fn=inp)  # type: ignore[arg-type]
    assert cfg.provider == "anthropic"
    # anthropic returns no live models → free-text kept
    assert cfg.model == "claude-x"
    assert cfg.mode == "plan"


def test_wizard_empty_answers_keep_existing() -> None:
    client = StubClient()
    existing = ForgeConfig(
        gateway_url="http://old:8000", provider="ollama", model="llama3", mode="plan"
    )
    inp = _scripted_input(["", "", "", ""])  # accept all defaults
    cfg = run_wizard(client, existing=existing, input_fn=inp)  # type: ignore[arg-type]
    assert cfg.gateway_url == "http://old:8000"
    assert cfg.provider == "ollama"
    assert cfg.mode == "plan"


def test_wizard_bad_mode_defaults_to_ask() -> None:
    client = StubClient()
    inp = _scripted_input(["http://gw:8000", "openai", "gpt-4o", "nonsense"])
    cfg = run_wizard(client, existing=ForgeConfig(), input_fn=inp)  # type: ignore[arg-type]
    assert cfg.mode == "ask"
