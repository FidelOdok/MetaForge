"""METAFORGE_APPROVALS=off turns approval holds off, in any environment.

The people who run a MetaForge can turn holds off for a fast loop (local
development, a staging soak). A write that would wait for a person then runs
at once, and the server logs why. Three kinds of call still wait: a step
that records a person's decision (it would write down an authority nobody
gave), approving a release, and placing an order (money cannot be taken
back). Those keep their own routes: a person, or the owner's delegation
switches.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_core.guardrails import (
    APPROVALS_ENV,
    Caller,
    approvals_off,
    decide,
    held_even_with_approvals_off,
)


@pytest.fixture
def off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(APPROVALS_ENV, "off")


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_the_switch_reads_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(APPROVALS_ENV, value)
    assert approvals_off() is True


@pytest.mark.parametrize("value", ["", "on", "true", "maybe"])
def test_anything_else_keeps_approvals_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(APPROVALS_ENV, value)
    assert approvals_off() is False


def test_it_applies_where_people_sign_in_too(monkeypatch: pytest.MonkeyPatch, off: None) -> None:
    monkeypatch.setenv("METAFORGE_AUTH_MODE", "supabase")
    assert approvals_off() is True


@pytest.mark.parametrize(
    "tool", ["library.register_component", "spice.run_simulation", "project.create"]
)
def test_a_held_write_runs_with_approvals_off(off: None, tool: str) -> None:
    decision = decide(tool, caller=Caller.REMOTE)
    assert decision.requires_approval is False
    assert decision.reason == f"approvals are off on this MetaForge ({APPROVALS_ENV}=off)"


def test_the_same_write_is_held_with_approvals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(APPROVALS_ENV, raising=False)
    assert decide("library.register_component", caller=Caller.REMOTE).requires_approval


@pytest.mark.parametrize(
    "tool", ["twin.imagine_accept", "twin.approve_release", "vendor.place_order"]
)
def test_a_persons_decision_a_release_and_an_order_still_wait(off: None, tool: str) -> None:
    assert held_even_with_approvals_off(tool)
    assert decide(tool, caller=Caller.REMOTE).requires_approval


def test_the_deployment_limits_say_so(off: None) -> None:
    from api_gateway.approvals.policy_routes import deployment_limits

    assert deployment_limits().approvals_off is True


async def test_the_chat_harness_does_not_hold_either(off: None) -> None:
    from orchestrator.harness.runtime import HarnessRuntime

    class Runs:
        def create(self, *_: Any, **__: Any) -> Any:
            raise AssertionError("a hold was opened with approvals off")

    runtime = HarnessRuntime.__new__(HarnessRuntime)
    runtime.runs = Runs()  # type: ignore[assignment]
    runtime.session_id = "s"
    spec = type("Spec", (), {"name": "library.register_component"})()
    await runtime._await_approval(spec, {})  # type: ignore[arg-type]
