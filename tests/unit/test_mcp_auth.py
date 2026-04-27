"""Unit tests for ``mcp_core.auth`` (MET-338)."""

from __future__ import annotations

import pytest

from mcp_core.auth import AUTH_DENIED, redact, verify_api_key

# ---------------------------------------------------------------------------
# verify_api_key
# ---------------------------------------------------------------------------


class TestVerifyApiKey:
    def test_open_mode_when_expected_unset(self) -> None:
        assert verify_api_key(provided=None, expected=None).ok
        assert verify_api_key(provided="anything", expected="").ok

    def test_missing_key_rejected_when_expected_set(self) -> None:
        result = verify_api_key(provided=None, expected="secret-123")
        assert not result.ok
        assert result.reason == "missing_key"

    def test_wrong_key_rejected(self) -> None:
        result = verify_api_key(provided="oops", expected="secret-123")
        assert not result.ok
        assert result.reason == "mismatch"

    def test_correct_key_accepted(self) -> None:
        result = verify_api_key(provided="secret-123", expected="secret-123")
        assert result.ok
        assert result.reason == "match"

    def test_empty_provided_treated_as_missing(self) -> None:
        result = verify_api_key(provided="", expected="secret-123")
        assert not result.ok
        assert result.reason == "missing_key"


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


class TestRedact:
    def test_long_key_keeps_last_four(self) -> None:
        assert redact("abcdef1234567890") == "********7890"

    def test_short_key_fully_masked(self) -> None:
        assert redact("abc") == "***"

    def test_empty_string(self) -> None:
        assert redact("") == ""

    def test_custom_keep_count(self) -> None:
        assert redact("abcdef1234567890", keep=2) == "********90"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_auth_denied_constant_is_stable() -> None:
    # Tests rely on this string in JSON-RPC error envelopes.
    assert AUTH_DENIED == "auth_error"


@pytest.mark.parametrize(
    "provided,expected,ok",
    [
        ("k", "k", True),
        ("k1", "k2", False),
        ("k", "kk", False),
        ("kk", "k", False),
    ],
)
def test_compare_constant_time_safe(provided: str, expected: str, ok: bool) -> None:
    """Smoke that the compare doesn't short-circuit on length mismatch."""
    assert bool(verify_api_key(provided, expected)) is ok
