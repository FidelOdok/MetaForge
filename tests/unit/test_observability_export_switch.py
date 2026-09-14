"""Turning telemetry off without turning tracing off (MET-701).

``api_gateway.server`` calls ``init_observability`` at module scope, so merely
importing the app stood up three live OTLP exporters aimed at
``http://localhost:4317``. In a test run nothing listens there, and the batch
processors spent ~33 seconds at interpreter shutdown trying to flush -- the
stall near the end of ``pytest tests/unit`` that read as a deadlock.

There are two switches, and keeping them separate is the point:

* ``OTEL_SDK_DISABLED`` (the OTel standard) turns the SDK off entirely. It
  also makes the SDK hand out NoOp tracers, so code under test records
  nothing -- which broke 15 instrumentation tests when the suite tried to use
  it as its off switch.
* ``METAFORGE_OTEL_EXPORT=off`` declines to build exporters and leaves tracing
  fully functional. That is what the suite wants, and what it now sets.
"""

from __future__ import annotations

import pytest

from observability.bootstrap import (
    export_disabled_by_env,
    init_observability,
    sdk_disabled_by_env,
)
from observability.config import ObservabilityConfig


class TestSdkDisabledParsing:
    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on", " true "])
    def test_truthy_spellings_disable(self, monkeypatch, value):
        monkeypatch.setenv("OTEL_SDK_DISABLED", value)
        assert sdk_disabled_by_env() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
    def test_falsy_spellings_do_not(self, monkeypatch, value):
        monkeypatch.setenv("OTEL_SDK_DISABLED", value)
        assert sdk_disabled_by_env() is False

    def test_absent_does_not_disable(self, monkeypatch):
        # The default has to stay "telemetry on" -- a deployment that never
        # sets this must keep exporting.
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        assert sdk_disabled_by_env() is False


class TestExportDisabledParsing:
    @pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "no", " off "])
    def test_off_spellings_disable_export(self, monkeypatch, value):
        monkeypatch.setenv("METAFORGE_OTEL_EXPORT", value)
        assert export_disabled_by_env() is True

    @pytest.mark.parametrize("value", ["on", "true", "1", "yes", ""])
    def test_on_spellings_keep_export(self, monkeypatch, value):
        monkeypatch.setenv("METAFORGE_OTEL_EXPORT", value)
        assert export_disabled_by_env() is False

    def test_absent_keeps_export(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_OTEL_EXPORT", raising=False)
        assert export_disabled_by_env() is False

    def test_the_two_switches_are_independent(self, monkeypatch):
        """Neither implies the other. Conflating them is exactly the mistake
        that made 15 instrumentation tests fail."""
        monkeypatch.setenv("METAFORGE_OTEL_EXPORT", "off")
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)

        assert export_disabled_by_env() is True
        assert sdk_disabled_by_env() is False


class TestInitObservability:
    def test_no_providers_are_built_when_export_is_off(self, monkeypatch):
        monkeypatch.setenv("METAFORGE_OTEL_EXPORT", "off")

        state = init_observability(ObservabilityConfig(enabled=True))

        # enabled=True in the config, and still nothing is constructed --
        # the point being that a test run cannot accidentally stand up
        # exporters through an app import.
        assert state.is_active is False
        assert state.tracer_provider is None
        assert state.meter_provider is None
        assert state.logger_provider is None

    def test_no_providers_are_built_when_the_sdk_is_disabled(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_OTEL_EXPORT", raising=False)
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

        state = init_observability(ObservabilityConfig(enabled=True))

        assert state.is_active is False

    def test_the_config_flag_still_works_on_its_own(self, monkeypatch):
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        monkeypatch.delenv("METAFORGE_OTEL_EXPORT", raising=False)

        state = init_observability(ObservabilityConfig(enabled=False))

        assert state.is_active is False


class TestTheSuitesOwnConfiguration:
    def test_export_is_off_for_this_run(self):
        """Guard the conftest line that saves ~100s per suite run.

        The three cases are deliberately distinguished. The conftest uses
        ``setdefault``, so the variable is *always present* during a normal
        run -- which means an **absent** variable can only mean that line was
        removed, and that is the regression worth failing on. A developer who
        deliberately profiles telemetry (``METAFORGE_OTEL_EXPORT=on``) gets a
        skip instead: a guard should catch an accident, not punish an
        intentional override.
        """
        import os

        raw = os.environ.get("METAFORGE_OTEL_EXPORT")
        if raw is None:
            pytest.fail(
                "METAFORGE_OTEL_EXPORT is unset — the conftest line that "
                "disables OTLP export during tests is gone. Expect the suite "
                "to take roughly twice as long (measured 202s vs 101s) and to "
                "stall at ~98% for ~33s while OTel flushes to a dead endpoint."
            )
        if not export_disabled_by_env():
            pytest.skip(f"export deliberately enabled (METAFORGE_OTEL_EXPORT={raw!r})")

        assert export_disabled_by_env() is True

    def test_but_the_sdk_itself_is_left_enabled(self):
        """So the instrumentation tests can still record real spans."""
        assert sdk_disabled_by_env() is False
