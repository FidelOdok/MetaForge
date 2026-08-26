"""Chat harness approval-timeout is configurable and long enough for a real
human to notice and approve out-of-band (no approval UI exists yet on either
the dashboard or the TUI -- MET-685)."""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import (
    _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS,
    chat_approval_timeout_seconds,
)


def test_default_is_long_enough_for_a_human_to_notice_and_approve() -> None:
    # The prior 120s default assumed a live approval UI a human could click
    # within two minutes -- none exists, so it silently denied every real
    # interactive session's twin.commit_geometry call (MET-685).
    assert _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS >= 1800.0
    assert chat_approval_timeout_seconds() == _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS", "300")
    assert chat_approval_timeout_seconds() == 300.0


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc"])
def test_bad_values_fall_back_to_default(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS", bad)
    assert chat_approval_timeout_seconds() == _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS
