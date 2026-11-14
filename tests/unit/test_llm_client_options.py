"""Provider SDK clients get a bounded wait (staging: a call hung a turn > 10 min)."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import adapters


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(adapters.REQUEST_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(adapters.SDK_RETRIES_ENV, raising=False)


def test_unset_keeps_the_sdk_defaults() -> None:
    assert adapters.client_options() == {}


def test_timeout_and_retries_are_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(adapters.REQUEST_TIMEOUT_ENV, "240")
    monkeypatch.setenv(adapters.SDK_RETRIES_ENV, "1")
    assert adapters.client_options() == {"timeout": 240.0, "max_retries": 1}


@pytest.mark.parametrize("raw", ["", "0", "-5", "soon"])
def test_a_meaningless_timeout_is_ignored(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(adapters.REQUEST_TIMEOUT_ENV, raw)
    assert "timeout" not in adapters.client_options()


def test_every_sdk_client_is_built_with_the_options() -> None:
    import inspect

    source = inspect.getsource(adapters)
    constructions = source.count("AsyncOpenAI(") + source.count("AsyncAnthropic(")
    assert constructions > 0
    assert source.count("**client_options()") == constructions
