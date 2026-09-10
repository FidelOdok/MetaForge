"""Two runtime bugs a type checker found, because nothing ran one (MET-733).

CI type-checked `shared/` and nothing else, while CLAUDE.md documented
`mypy --strict` with zero errors as the bar for every module. Once `mypy .`
was made runnable at all, it turned out to be sitting on these.

Neither is a typing nicety. Both fail at runtime.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


class TestWorkerLoggingIsActuallyConfigured:
    """``configure_logging()`` was called with a required argument missing.

    The TypeError went straight into a bare ``except Exception: pass``, so the
    Temporal worker has never had its logging configured -- no JSON rendering,
    no trace-context processor, no console handler. It ran on structlog's
    defaults, which is why its output does not parse under Loki's ``| json``
    filter. The same swallowed-failure shape as MET-724/726/727/728.
    """

    def test_configure_logging_requires_a_config_argument(self):
        """The premise. If this signature ever grows a default, the bug below
        stops being a bug and this test says so rather than silently passing."""
        from observability.logging import configure_logging

        params = inspect.signature(configure_logging).parameters
        assert "config" in params
        assert params["config"].default is inspect.Parameter.empty, (
            "config now has a default; the MET-733 call-site bug is moot"
        )

    @pytest.mark.asyncio
    async def test_the_worker_passes_a_config(self, monkeypatch):
        """Drive the worker's real ``main()`` far enough to reach the call."""
        import orchestrator.temporal_worker as worker

        received: list[object] = []

        def _spy(config):  # noqa: ANN001, ANN202
            received.append(config)

        monkeypatch.setattr("observability.logging.configure_logging", _spy)

        # Stop main() right after the logging setup: the next thing it does is
        # bind the consolidation stack, so failing there is a clean exit point
        # that still proves the call above happened.
        async def _stop(*_a: object, **_k: object):
            raise RuntimeError("stop here")

        monkeypatch.setattr(worker, "_bind_consolidation", _stop)

        with pytest.raises(RuntimeError, match="stop here"):
            await worker.main()

        assert received, "configure_logging was never reached"
        config = received[0]
        # A TypeError from a missing argument would have been swallowed and
        # `received` would hold nothing -- so reaching here at all is the fix.
        assert getattr(config, "service_name", None) == "metaforge-temporal-worker", (
            "the worker must not label its telemetry as the gateway; "
            f"got {getattr(config, 'service_name', None)!r}"
        )

    @pytest.mark.asyncio
    async def test_a_logging_failure_is_reported_not_swallowed(self, monkeypatch, caplog):
        """The handler stays -- logging config must not stop the worker -- but
        it no longer hides the failure, which is what let this survive."""
        import orchestrator.temporal_worker as worker

        def _boom(_config):  # noqa: ANN001, ANN202
            raise RuntimeError("no handlers for you")

        monkeypatch.setattr("observability.logging.configure_logging", _boom)

        warned: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            worker.logger,
            "warning",
            lambda event, **kw: warned.append((event, kw)),
        )

        async def _stop(*_a: object, **_k: object):
            raise RuntimeError("stop here")

        monkeypatch.setattr(worker, "_bind_consolidation", _stop)

        with pytest.raises(RuntimeError, match="stop here"):
            await worker.main()

        assert any(ev == "worker_logging_config_failed" for ev, _ in warned), (
            f"a swallowed logging failure must still be reported; saw {warned}"
        )


class TestACancelledStepDoesNotCrashTheWorkflow:
    """``isinstance(result, Exception)`` after ``gather(return_exceptions=True)``.

    ``asyncio.CancelledError`` has inherited from ``BaseException``, not
    ``Exception``, since Python 3.8 -- so a cancelled step took the *else*
    branch and died on ``result.status`` with an AttributeError instead of
    being recorded as failed.
    """

    def test_cancelled_error_is_not_an_exception(self):
        """The premise, pinned: this is the language fact the bug rested on."""
        assert issubclass(asyncio.CancelledError, BaseException)
        assert not issubclass(asyncio.CancelledError, Exception)

    def test_gather_returns_cancelled_error_as_a_result(self):
        """And it really does come back as a value, not a raise."""

        async def _cancelled() -> None:
            raise asyncio.CancelledError

        async def _run():
            return await asyncio.gather(_cancelled(), return_exceptions=True)

        results = asyncio.run(_run())

        assert len(results) == 1
        assert isinstance(results[0], asyncio.CancelledError)
        # The narrowing the old code used, shown failing to catch it.
        assert not isinstance(results[0], Exception)

    def test_the_workflow_narrows_on_baseexception(self):
        """The source must narrow on BaseException at that site.

        Asserting on source is crude, but the alternative is standing up a
        whole Temporal workflow execution to observe one isinstance call. The
        two tests above pin *why* it matters; this pins that it is done.
        """
        from pathlib import Path

        import orchestrator.workflows.hardware_design_workflow as wf

        source = Path(wf.__file__).read_text(encoding="utf-8")

        assert "isinstance(result, BaseException)" in source, (
            "the gather result must be narrowed on BaseException, or a "
            "cancelled step crashes on .status"
        )
        assert "isinstance(result, Exception)" not in source, (
            "an Exception-only narrowing has come back; CancelledError escapes it"
        )


class TestWorkerExportsItsTelemetry:
    """MET-734: `configure_logging` alone was never enough.

    It builds the structlog chain and a console handler. `init_observability`
    is what constructs the OTLP exporters and attaches the OTel
    LoggingHandler. The worker called neither, so it emitted nothing to the
    collector -- while docker-compose.yml passed it an
    OTEL_EXPORTER_OTLP_ENDPOINT that no code in the process read.

    Found by checking a claim I had made in MET-733's commit message rather
    than assuming it: Loki's service_name values were still only
    ["metaforge-gateway"] after that deploy.
    """

    @pytest.mark.asyncio
    async def test_the_worker_initialises_observability(self, monkeypatch):
        import orchestrator.temporal_worker as worker

        seen: list[object] = []

        def _spy(config):  # noqa: ANN001, ANN202
            seen.append(config)

            class _State:
                is_active = False

            return _State()

        monkeypatch.setattr("observability.bootstrap.init_observability", _spy)

        async def _stop(*_a: object, **_k: object):
            raise RuntimeError("stop here")

        monkeypatch.setattr(worker, "_bind_consolidation", _stop)

        with pytest.raises(RuntimeError, match="stop here"):
            await worker.main()

        assert seen, "init_observability was never called; the worker exports nothing"
        assert getattr(seen[0], "service_name", None) == "metaforge-temporal-worker"

    @pytest.mark.asyncio
    async def test_the_endpoint_from_the_environment_is_used(self, monkeypatch):
        """The variable compose passes must actually reach the exporter --
        being passed and unread is the whole bug."""
        import orchestrator.temporal_worker as worker

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example:4317")

        seen: list[object] = []
        monkeypatch.setattr(
            "observability.bootstrap.init_observability",
            lambda config: (seen.append(config), type("S", (), {"is_active": False}))[1],
        )

        async def _stop(*_a: object, **_k: object):
            raise RuntimeError("stop here")

        monkeypatch.setattr(worker, "_bind_consolidation", _stop)

        with pytest.raises(RuntimeError, match="stop here"):
            await worker.main()

        assert seen
        assert seen[0].otlp.endpoint == "http://collector.example:4317"

    @pytest.mark.asyncio
    async def test_telemetry_is_flushed_on_shutdown(self, monkeypatch):
        """Without the flush, a shutting-down worker's last spans and logs
        never leave the process -- which is the failure mode that looks like
        'the worker just stops logging near the end'."""
        import orchestrator.temporal_worker as worker

        flushed: list[object] = []

        class _State:
            is_active = True

        monkeypatch.setattr("observability.bootstrap.init_observability", lambda _c: _State())
        monkeypatch.setattr(
            "observability.bootstrap.shutdown_observability",
            lambda state: flushed.append(state),
        )

        async def _no_stack(*_a: object, **_k: object):
            return None

        async def _client(*_a: object, **_k: object):
            return object()

        async def _run(*_a: object, **_k: object) -> None:
            return None

        monkeypatch.setattr(worker, "_bind_consolidation", _no_stack)
        monkeypatch.setattr(worker, "connect_client", _client)
        monkeypatch.setattr(worker, "run_worker", _run)

        await worker.main()

        assert flushed, "shutdown_observability was never called; telemetry is lost on exit"

    @pytest.mark.asyncio
    async def test_a_failing_init_does_not_stop_the_worker(self, monkeypatch):
        """Telemetry is not worth refusing to run agent workflows over -- but
        the failure is reported, not swallowed."""
        import orchestrator.temporal_worker as worker

        def _boom(_config):  # noqa: ANN001, ANN202
            raise RuntimeError("collector unreachable")

        monkeypatch.setattr("observability.bootstrap.init_observability", _boom)

        warned: list[str] = []
        monkeypatch.setattr(worker.logger, "warning", lambda event, **_kw: warned.append(event))

        async def _stop(*_a: object, **_k: object):
            raise RuntimeError("stop here")

        monkeypatch.setattr(worker, "_bind_consolidation", _stop)

        # Reaching _bind_consolidation at all proves init's failure did not
        # propagate.
        with pytest.raises(RuntimeError, match="stop here"):
            await worker.main()

        assert "worker_observability_init_failed" in warned
