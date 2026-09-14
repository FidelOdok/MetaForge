"""The Temporal worker can actually be started (MET-186 wiring).

`create_worker` / `run_worker` have existed since 2026-03-08 with no caller
outside tests and no client connect anywhere in the tree, so the Temporal
server ran with **zero workflow executions** for six months while
`TEMPORAL_HOST` was passed to the gateway container and read by nothing.

These tests pin the last mile: host resolution, a retrying connect, and the
registration set a worker will serve.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.temporal_worker import (
    ALL_ACTIVITIES,
    ALL_WORKFLOWS,
    DEFAULT_NAMESPACE,
    DEFAULT_TEMPORAL_HOST,
    connect_client,
    temporal_host,
    temporal_namespace,
)


def test_host_comes_from_the_env_docker_compose_already_sets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    assert temporal_host() == DEFAULT_TEMPORAL_HOST

    # This is the variable compose has been passing to the gateway all along.
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    assert temporal_host() == "temporal:7233"

    # Blank must not produce an empty target.
    monkeypatch.setenv("TEMPORAL_HOST", "   ")
    assert temporal_host() == DEFAULT_TEMPORAL_HOST


def test_namespace_is_configurable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TEMPORAL_NAMESPACE", raising=False)
    assert temporal_namespace() == DEFAULT_NAMESPACE

    monkeypatch.setenv("TEMPORAL_NAMESPACE", "metaforge")
    assert temporal_namespace() == "metaforge"


def test_the_consolidation_workflow_is_registered():
    # MET-567's pass had a Temporal workflow since MET-454 and was never in
    # this list, so even a running worker could not have served it.
    assert {c.__name__ for c in ALL_WORKFLOWS} == {
        "SingleAgentWorkflow",
        "HardwareDesignWorkflow",
        "ConsolidationWorkflow",
    }
    assert "run_consolidation_pass_activity" in {getattr(a, "__name__", "") for a in ALL_ACTIVITIES}


class TestWorkflowSandbox:
    """Every registered workflow must survive Temporal's sandbox validation.

    Caught by actually starting a worker: construction died with
    ``RuntimeError: Failed validating workflow SingleAgentWorkflow``, because
    the sandbox re-imports each workflow module and ``structlog`` pulls in
    ``rich``, whose ``style.py`` runs ``count(getrandbits(24))`` at module
    scope. So the workflows were not merely un-started for six months -- they
    were **un-runnable by any worker**, which is why the server's execution
    history is empty.
    """

    def test_passthrough_covers_the_import_unclean_dependencies(self):
        from orchestrator.temporal_worker import PASSTHROUGH_MODULES

        # rich is the actual offender; structlog is how we reach it.
        assert "rich" in PASSTHROUGH_MODULES
        assert "structlog" in PASSTHROUGH_MODULES

    @pytest.mark.asyncio
    async def test_every_registered_workflow_passes_sandbox_validation(self):
        """Run the sandbox over each workflow, exactly as a worker would.

        An earlier version of this test asserted that ``create_worker`` with a
        MagicMock raised ``TypeError`` (client) rather than ``RuntimeError``
        (validation) -- but the client check happens *before* workflow
        validation, so it never reached the thing it claimed to prove and
        passed while ConsolidationWorkflow was still broken. This calls
        ``prepare_workflow`` directly, which is what actually fails, and needs
        a running loop -- hence async.
        """
        pytest.importorskip("temporalio")
        from temporalio.workflow import _Definition

        from orchestrator.temporal_worker import ALL_WORKFLOWS, workflow_runner

        runner = workflow_runner()
        for wf in ALL_WORKFLOWS:
            defn = _Definition.must_from_class(wf)
            # Raises RestrictedWorkflowAccessError if an import in the
            # workflow's chain touches non-determinism at module scope.
            runner.prepare_workflow(defn)


class TestConnectRetry:
    """A worker container and its server start together, so a first-attempt
    failure is usually a race — the MET-710 lesson applied to Temporal.

    These patch ``temporalio.client.Client``, so they need the SDK. It is in
    the ``dev`` extra deliberately (CI must exercise the durable tiers, not
    skip them), but a stripped env should skip rather than error.
    """

    @pytest.fixture(autouse=True)
    def _require_sdk(self):
        pytest.importorskip("temporalio.client")

    @pytest.mark.asyncio
    async def test_a_transient_failure_is_retried_and_recovers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = {"n": 0}
        slept: list[float] = []

        class _Client:
            @staticmethod
            async def connect(target: str, namespace: str = "default") -> Any:
                calls["n"] += 1
                if calls["n"] < 3:
                    raise OSError("failed to connect to all addresses")
                return f"client:{target}/{namespace}"

        import temporalio.client as tc

        monkeypatch.setattr(tc, "Client", _Client)

        async def _sleep(seconds: float) -> None:
            slept.append(seconds)

        client = await connect_client("temporal:7233", "default", attempts=5, sleep=_sleep)

        assert client == "client:temporal:7233/default"
        assert calls["n"] == 3
        assert slept == [2.0, 4.0]  # backoff, not a busy loop

    @pytest.mark.asyncio
    async def test_a_permanent_failure_raises_after_its_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        class _Client:
            @staticmethod
            async def connect(target: str, namespace: str = "default") -> Any:
                raise OSError("no route to host")

        import temporalio.client as tc

        monkeypatch.setattr(tc, "Client", _Client)

        async def _sleep(seconds: float) -> None:
            return None

        with pytest.raises(OSError, match="no route to host"):
            await connect_client("temporal:7233", attempts=3, sleep=_sleep)

    @pytest.mark.asyncio
    async def test_backoff_is_capped(self, monkeypatch: pytest.MonkeyPatch):
        slept: list[float] = []

        class _Client:
            @staticmethod
            async def connect(target: str, namespace: str = "default") -> Any:
                raise OSError("down")

        import temporalio.client as tc

        monkeypatch.setattr(tc, "Client", _Client)

        async def _sleep(seconds: float) -> None:
            slept.append(seconds)

        with pytest.raises(OSError):
            await connect_client("temporal:7233", attempts=9, sleep=_sleep)

        # Never unbounded: a worker retrying forever at growing intervals is
        # indistinguishable from a hung one.
        assert max(slept) == 10.0
