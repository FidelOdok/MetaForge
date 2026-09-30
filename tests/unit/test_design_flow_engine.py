"""Choosing an engine, and what happens when it is not there (FORGE-401).

The behaviour these protect is a refusal. That makes them easy to write
badly: almost any implementation returns *something* when Temporal is down,
and the one that returns a started-looking run is the one that caused this
ticket. So the assertions here are about what does **not** exist afterwards.
"""

from __future__ import annotations

import pytest

from api_gateway.runs.engine import (
    FLOW_ENGINE_ENV,
    FlowEngine,
    resolve_flow_engine,
    temporal_target,
)
from orchestrator.design_flow.frozen import FrozenFlow, FrozenPhase, freeze_flow
from orchestrator.design_flow.launcher import TemporalUnavailableError, workflow_id_for
from orchestrator.harness.runs import InMemoryRunStore


class TestEngineSelection:
    def test_temporal_is_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(FLOW_ENGINE_ENV, raising=False)
        assert resolve_flow_engine() is FlowEngine.TEMPORAL

    def test_the_in_process_double_must_be_asked_for(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(FLOW_ENGINE_ENV, "in_process")
        assert resolve_flow_engine() is FlowEngine.IN_PROCESS

    def test_an_unknown_engine_is_an_error_not_a_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "I set the env var and nothing changed" is how a deployment ends
        up running the engine it thought it had turned off."""
        monkeypatch.setenv(FLOW_ENGINE_ENV, "celery")
        with pytest.raises(ValueError, match="not a valid engine"):
            resolve_flow_engine()

    def test_the_in_process_engine_warns_every_time_it_is_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment that lands here by accident has to keep saying so.
        A warning at start-up only is a warning nobody sees on day three.

        Captured through ``structlog.testing``, not ``caplog``: this codebase
        logs via structlog, which does not route to the stdlib handler
        pytest's fixture reads. A first version of this test used ``caplog``,
        saw zero records, and would have passed the moment the warning was
        deleted -- it was asserting on an empty list either way.
        """
        from structlog.testing import capture_logs

        monkeypatch.setenv(FLOW_ENGINE_ENV, "in_process")
        with capture_logs() as logs:
            resolve_flow_engine()
            resolve_flow_engine()
        warnings = [e for e in logs if e.get("event") == "design_flow_engine_in_process"]
        assert len(warnings) == 2
        assert "NOT durable" in warnings[0]["detail"]

    def test_the_target_comes_from_the_variable_compose_already_sets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
        assert temporal_target() == "temporal:7233"

    def test_there_is_a_local_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEMPORAL_HOST", raising=False)
        assert temporal_target() == "localhost:7233"


class TestTheRefusalIsLegible:
    def test_it_says_no_run_was_created(self) -> None:
        exc = TemporalUnavailableError("localhost:7233", "connection refused")
        assert "no run was created" in str(exc)

    def test_it_says_there_is_no_fallback_on_purpose(self) -> None:
        """Whoever reads this error will wonder why it did not just run
        in-process. The answer belongs in the message, not in a commit."""
        exc = TemporalUnavailableError("localhost:7233", "connection refused")
        assert "no in-process fallback" in str(exc)

    def test_it_says_how_to_fix_it(self) -> None:
        exc = TemporalUnavailableError("localhost:7233", "connection refused")
        assert "docker compose up temporal" in str(exc)


class TestNoPhantomRun:
    def test_a_deleted_run_leaves_nothing_behind(self) -> None:
        """A record stuck in ``queued`` that nothing will pick up reads as
        "starting" to everyone looking at the list, and nothing notices that
        it never moves."""
        store = InMemoryRunStore()
        run = store.create({"flow": "hardware_v1", "goal": "g"})
        assert len(store.list()) == 1
        store.delete(run.id)
        assert store.list() == []

    def test_deleting_an_unknown_run_is_not_an_error(self) -> None:
        # The delete happens on a failure path; it must not raise a second
        # exception over the one being reported.
        InMemoryRunStore().delete("does-not-exist")


class TestTheFlowIsPinnedToTheRun:
    def test_the_workflow_id_is_derived_from_the_run(self) -> None:
        assert workflow_id_for("abc") == "design-flow-abc"

    def test_freezing_captures_a_hash(self) -> None:
        from orchestrator.design_flow.spec import get_flow

        frozen = freeze_flow(get_flow("hardware_v1"))
        assert frozen.content_hash
        frozen.verify()

    def test_an_edited_flow_no_longer_verifies(self) -> None:
        """Why the flow travels as data rather than a module lookup: editing
        ``spec.py`` must not change what an in-flight run is doing."""
        flow = FrozenFlow(
            template_id="t",
            name="T",
            phases=[FrozenPhase(id="p", title="P", objective="o")],
        )
        flow.content_hash = flow.compute_hash()
        flow.verify()
        flow.phases[0].objective = "something else"
        with pytest.raises(ValueError, match="does not match the hash"):
            flow.verify()

    def test_an_unhashed_flow_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no content_hash"):
            FrozenFlow(template_id="t", name="T", phases=[]).verify()

    def test_the_hash_ignores_field_order(self) -> None:
        # Otherwise a harmless refactor of the dataclass invalidates every
        # in-flight run, and the check gets disabled the first time it does.
        a = freeze_flow(_definition())
        b = freeze_flow(_definition())
        assert a.content_hash == b.content_hash


class _Gate:
    name = "g"
    auto_approve = False
    criteria = ()
    enforce_constraints = False
    gate_id = None


class _Phase:
    id = "p"
    title = "P"
    objective = "o"
    expected_artifacts = ()
    required_deliverables = ()
    enforce_deliverables = True
    disciplines = ()
    gate = _Gate()


class _Definition:
    id = "t"
    name = "T"
    phases = (_Phase(),)


def _definition() -> _Definition:
    return _Definition()
