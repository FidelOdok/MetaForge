"""Failure classes and their responses (FORGE-539)."""

from __future__ import annotations

import pytest

from orchestrator.design_flow.failures import (
    FailureClass,
    FailureResponse,
    classify_failure,
    response_for,
)


@pytest.mark.parametrize(
    ("message", "error_type", "expected"),
    [
        ("connection reset by peer", "", FailureClass.TRANSIENT),
        ("upstream returned 429 rate limit", "", FailureClass.TRANSIENT),
        ("no model provider could serve this phase: missing API key", "", FailureClass.ENVIRONMENT),
        ("x", "ProviderUnavailable", FailureClass.ENVIRONMENT),
        ("MCP error -32001: freecad adapter unavailable", "", FailureClass.TOOL),
        ("unknown tool spice.run_simulation", "", FailureClass.CAPABILITY),
        ("CAD file not found at /tmp/a.step", "", FailureClass.DATA),
        ("incompatible units: mm vs N", "", FailureClass.DATA),
        ("safety factor 1.4 < 2.0", "", FailureClass.DESIGN),
        ("held: approval_required", "", FailureClass.AUTHORIZATION),
        ("reply flagged ungrounded", "", FailureClass.REASONING),
        ("the community forum says hi", "", FailureClass.UNKNOWN),
        ("costs 1500 GBP", "", FailureClass.UNKNOWN),
    ],
)
def test_classify(message: str, error_type: str, expected: FailureClass) -> None:
    assert classify_failure(message, error_type=error_type).failure_class is expected


def test_approval_timeout_is_authorization_not_transient() -> None:
    # Order matters: the more specific class wins.
    assert (
        classify_failure("approval_required: timed out").failure_class is FailureClass.AUTHORIZATION
    )


def test_only_transient_failures_are_retried_as_is() -> None:
    retryable = {c for c in FailureClass if response_for(c).retryable}
    assert retryable == {FailureClass.TRANSIENT}


def test_a_design_failure_replans_rather_than_retries() -> None:
    verdict = response_for(FailureClass.DESIGN)
    assert verdict.response is FailureResponse.REPLAN
    assert verdict.as_dict()["retryable"] is False


def test_every_class_has_a_policy() -> None:
    for failure_class in FailureClass:
        assert response_for(failure_class).guidance
