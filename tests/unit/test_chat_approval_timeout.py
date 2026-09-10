"""Chat harness approval-timeout is configurable and long enough for a real
human to notice and approve out-of-band (no approval UI exists yet on either
the dashboard or the TUI -- MET-685).

Design-flow's approval-timeout is the opposite: short by default, since that
surface is fully unattended and has no approver at all (MET-707)."""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import (
    _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS,
    _DEFAULT_DESIGN_FLOW_APPROVAL_TIMEOUT_SECONDS,
    chat_approval_timeout_seconds,
    design_flow_approval_timeout_seconds,
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


def test_design_flow_default_is_short_since_nothing_ever_approves_it() -> None:
    # Design-flow (ReActPhaseBrain) drives phases through the same
    # run_chat_turn harness but nothing ever resolves a design-flow-
    # originated /v1/chat/tool_approvals entry -- inheriting chat's 1800s
    # default meant every phase that recorded a decision stalled for up to
    # 30 minutes before failing gracefully (MET-707).
    assert _DEFAULT_DESIGN_FLOW_APPROVAL_TIMEOUT_SECONDS < 60.0
    assert design_flow_approval_timeout_seconds() == _DEFAULT_DESIGN_FLOW_APPROVAL_TIMEOUT_SECONDS


def test_design_flow_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_DESIGN_FLOW_APPROVAL_TIMEOUT_SECONDS", "5")
    assert design_flow_approval_timeout_seconds() == 5.0


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc"])
def test_design_flow_bad_values_fall_back_to_default(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("METAFORGE_DESIGN_FLOW_APPROVAL_TIMEOUT_SECONDS", bad)
    assert design_flow_approval_timeout_seconds() == _DEFAULT_DESIGN_FLOW_APPROVAL_TIMEOUT_SECONDS
