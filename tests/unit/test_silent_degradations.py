"""Load-bearing failures must leave a trace (MET-728).

Three fallbacks whose behaviour is right and whose silence was not. Each test
pins both halves: the fallback is unchanged, *and* it now says so. Behaviour
without diagnosability is how six separate bugs survived in this codebase
today -- a graceful fallback with no alarm becomes the permanent state.

These capture logs by swapping the *module's* ``logger`` attribute rather than
reconfiguring structlog globally. The first version of this file did the
latter and passed in isolation while failing in the full suite: structlog
caches a bound logger on first use, so once an earlier test has used
``gate_eval``'s module-level logger, replacing the processor chain no longer
reaches it. Swapping the attribute is order-independent and mutates no global
state, which also keeps these tests from perturbing anyone else's.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest


class _RecordingLogger:
    """Records structlog-style calls without touching global configuration."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str):  # noqa: ANN202
        def _log(event: str, **kw: Any) -> None:
            self.calls.append((level, event, kw))

        return _log

    def __getattr__(self, name: str):  # noqa: ANN204
        if name in {"debug", "info", "warning", "error", "critical", "exception"}:
            return self._record(name)
        raise AttributeError(name)

    def events(self, name: str, level: str = "warning") -> list[dict[str, Any]]:
        return [kw for lvl, ev, kw in self.calls if ev == name and lvl == level]


@pytest.fixture
def gate_logger(monkeypatch):
    from api_gateway.runs import gate_eval

    recorder = _RecordingLogger()
    monkeypatch.setattr(gate_eval, "logger", recorder)
    return recorder


@pytest.fixture
def recorder_logger(monkeypatch):
    from api_gateway.twin import geometry_recorder

    recorder = _RecordingLogger()
    monkeypatch.setattr(geometry_recorder, "logger", recorder)
    return recorder


class TestGateLoadabilityUnknown:
    """``_is_loadable`` must distinguish "checked, absent" from "couldn't check"."""

    @pytest.mark.asyncio
    async def test_a_raising_lookup_still_fails_closed(self, gate_logger):
        from api_gateway.runs.gate_eval import _is_loadable

        class _Exploding:
            async def get_work_product(self, _id):  # noqa: ANN001, ANN202
                raise RuntimeError("neo4j unreachable")

        # Fail-closed is the whole point: an unverifiable model must not
        # satisfy a gate. This assertion is what keeps the fix from drifting
        # into "log it and let it through".
        assert await _is_loadable(_Exploding(), uuid4()) is False

        warnings = gate_logger.events("gate_eval_loadability_unknown")
        assert warnings, "an unverifiable gate input must leave a trace"
        assert "neo4j unreachable" in warnings[0]["error"]

    @pytest.mark.asyncio
    async def test_a_genuinely_missing_blob_is_not_reported_as_unknown(self, gate_logger):
        """The two causes want opposite responses -- regenerate vs retry -- so
        a real absence must not borrow the unknown-lookup event."""
        from api_gateway.runs.gate_eval import _is_loadable

        class _Empty:
            async def get_work_product(self, _id):  # noqa: ANN001, ANN202
                return None

        assert await _is_loadable(_Empty(), uuid4()) is False
        assert not gate_logger.events("gate_eval_loadability_unknown")

    @pytest.mark.asyncio
    async def test_no_twin_still_fails_open_silently(self, gate_logger):
        """Non-twin setups keep their prior behaviour, and that is not a
        degradation worth warning about -- warning here would make the signal
        routine, which is what made the old info-level logging useless."""
        from api_gateway.runs.gate_eval import _is_loadable

        assert await _is_loadable(None, uuid4()) is True
        assert not gate_logger.events("gate_eval_loadability_unknown")


class TestGateProjectScoping:
    @pytest.mark.asyncio
    async def test_a_raising_project_lookup_is_reported(self, gate_logger):
        from api_gateway.runs.gate_eval import TwinConstraintChecker

        class _Exploding:
            async def get_project(self, _pid):  # noqa: ANN001, ANN202
                raise RuntimeError("postgres pool exhausted")

        checker = TwinConstraintChecker(twin=object(), backend=_Exploding())

        scope = await checker._project_wp_ids("proj-1")

        # None means unscopable, which counts EVERY violation -- stricter, so
        # safe, but previously invisible.
        assert scope is None
        warnings = gate_logger.events("gate_eval_project_scoping_failed")
        assert warnings
        assert "postgres pool exhausted" in warnings[0]["error"]
        assert "fails closed" in warnings[0]["consequence"]

    @pytest.mark.asyncio
    async def test_an_unscopable_request_is_not_a_failure(self, gate_logger):
        """No project id, or no backend, is ordinary -- not a degradation."""
        from api_gateway.runs.gate_eval import TwinConstraintChecker

        checker = TwinConstraintChecker(twin=object(), backend=None)

        assert await checker._project_wp_ids("proj-1") is None
        assert await checker._project_wp_ids(None) is None
        assert not gate_logger.events("gate_eval_project_scoping_failed")


class TestSupersedesChain:
    @pytest.mark.asyncio
    async def test_a_raising_lookup_is_reported_and_still_commits(self, recorder_logger):
        from api_gateway.twin.geometry_recorder import _find_current_work_product
        from twin_core.models.enums import WorkProductType

        class _Exploding:
            async def list_work_products(self, **_kw):  # noqa: ANN003, ANN202
                raise RuntimeError("bolt handshake failed")

        result = await _find_current_work_product(
            _Exploding(), WorkProductType.CAD_MODEL, "Gimbal Base", str(uuid4())
        )

        # Unchanged: a commit must not fail over its own history.
        assert result is None
        warnings = recorder_logger.events("geometry_predecessor_lookup_failed")
        assert warnings, "a silently broken provenance chain is the wrong thing to be quiet about"
        assert "bolt handshake failed" in warnings[0]["error"]
        assert "SUPERSEDES" in warnings[0]["consequence"]

    @pytest.mark.asyncio
    async def test_no_project_id_is_not_a_failure(self, recorder_logger):
        from api_gateway.twin.geometry_recorder import _find_current_work_product
        from twin_core.models.enums import WorkProductType

        result = await _find_current_work_product(object(), WorkProductType.CAD_MODEL, "Base", None)

        assert result is None
        assert not recorder_logger.events("geometry_predecessor_lookup_failed")
