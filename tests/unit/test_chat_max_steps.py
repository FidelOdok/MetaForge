"""Chat tool-call budget is configurable and no longer stuck at 6 (MET-10)."""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import _DEFAULT_CHAT_MAX_STEPS, chat_max_steps


def test_default_is_generous_enough_for_agentic_work() -> None:
    # 6 was too few for a multi-part CAD assembly (~15-20 tool calls).
    assert _DEFAULT_CHAT_MAX_STEPS >= 20
    assert chat_max_steps() == _DEFAULT_CHAT_MAX_STEPS


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_CHAT_MAX_STEPS", "50")
    assert chat_max_steps() == 50


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc", "12.5"])
def test_bad_values_fall_back_to_default(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("METAFORGE_CHAT_MAX_STEPS", bad)
    assert chat_max_steps() == _DEFAULT_CHAT_MAX_STEPS
