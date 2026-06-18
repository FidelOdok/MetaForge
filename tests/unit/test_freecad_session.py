"""Unit tests for the stateful FreeCAD session store (MET-528).

The store is FreeCAD-agnostic: document creation/teardown and the clock are
injected, so the full lifecycle (open/close, object registry, idle-TTL eviction,
capacity eviction) is exercised here without FreeCAD bindings installed.
"""

from __future__ import annotations

import pytest

from tool_registry.tools.freecad.session import (
    DEFAULT_TTL_SECONDS,
    FreecadSessionStore,
    ObjectNotFoundError,
    SessionNotFoundError,
)


class _FakeDoc:
    """Stand-in for a FreeCAD document; records close calls."""

    def __init__(self, name: str) -> None:
        self.Name = name
        self.closed = False


class _Clock:
    """Manually-advanced monotonic clock."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _store(**kw: object) -> tuple[FreecadSessionStore, list[_FakeDoc]]:
    created: list[_FakeDoc] = []

    def factory(name: str) -> _FakeDoc:
        doc = _FakeDoc(name)
        created.append(doc)
        return doc

    def closer(doc: _FakeDoc) -> None:
        doc.closed = True

    store = FreecadSessionStore(doc_factory=factory, doc_closer=closer, **kw)  # type: ignore[arg-type]
    return store, created


class TestSessionLifecycle:
    def test_open_returns_id_and_creates_document(self) -> None:
        store, created = _store()
        sid = store.open_session(name="widget")
        assert sid
        assert len(created) == 1
        assert created[0].Name == "widget"
        assert store.describe(sid)["name"] == "widget"

    def test_close_tears_down_document(self) -> None:
        store, created = _store()
        sid = store.open_session()
        assert store.close_session(sid) is True
        assert created[0].closed is True
        # Session is gone afterwards.
        with pytest.raises(SessionNotFoundError):
            store.get(sid)

    def test_close_unknown_returns_false(self) -> None:
        store, _ = _store()
        assert store.close_session("nope") is False

    def test_get_unknown_raises(self) -> None:
        store, _ = _store()
        with pytest.raises(SessionNotFoundError):
            store.get("ghost")

    def test_sessions_are_isolated(self) -> None:
        store, _ = _store()
        a = store.open_session("a")
        b = store.open_session("b")
        store.register_object(a, object(), "body", "BodyA")
        assert store.describe(a)["object_count"] == 1
        assert store.describe(b)["object_count"] == 0


class TestObjectRegistry:
    def test_register_returns_stable_addressable_ids(self) -> None:
        store, _ = _store()
        sid = store.open_session()
        sentinel1 = object()
        sentinel2 = object()
        id1 = store.register_object(sid, sentinel1, "body", "Body")
        id2 = store.register_object(sid, sentinel2, "sketch", "Sketch")
        assert id1 == "body_1"
        assert id2 == "sketch_2"
        assert store.get_object(sid, id1) is sentinel1
        assert store.get_object(sid, id2) is sentinel2

    def test_get_missing_object_raises(self) -> None:
        store, _ = _store()
        sid = store.open_session()
        with pytest.raises(ObjectNotFoundError):
            store.get_object(sid, "body_99")

    def test_describe_lists_objects_in_creation_order(self) -> None:
        store, _ = _store()
        sid = store.open_session()
        store.register_object(sid, object(), "body", "B")
        store.register_object(sid, object(), "sketch", "S")
        objs = store.describe(sid)["objects"]
        assert [o["obj_id"] for o in objs] == ["body_1", "sketch_2"]
        assert [o["order"] for o in objs] == [1, 2]


class TestEviction:
    def test_idle_ttl_evicts_and_closes(self) -> None:
        clock = _Clock()
        store, created = _store(ttl_seconds=100, clock=clock)
        sid = store.open_session()
        clock.advance(101)
        # Any access triggers eviction sweep.
        with pytest.raises(SessionNotFoundError):
            store.get(sid)
        assert created[0].closed is True

    def test_access_refreshes_idle_timer(self) -> None:
        clock = _Clock()
        store, _ = _store(ttl_seconds=100, clock=clock)
        sid = store.open_session()
        clock.advance(60)
        store.get(sid)  # refresh
        clock.advance(60)  # 120 total, but only 60 since last access
        assert store.get(sid).session_id == sid

    def test_capacity_evicts_least_recently_used(self) -> None:
        clock = _Clock()
        store, _ = _store(max_sessions=2, ttl_seconds=0, clock=clock)
        a = store.open_session("a")
        clock.advance(1)
        b = store.open_session("b")
        clock.advance(1)
        store.get(a)  # 'a' is now most-recently-used; 'b' is LRU
        clock.advance(1)
        store.open_session("c")  # at capacity → evict LRU ('b')
        assert a in store.session_ids()
        with pytest.raises(SessionNotFoundError):
            store.get(b)


class TestDefaultFactoryDegradation:
    def test_default_factory_raises_without_freecad(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # With real FreeCAD bindings absent, opening a session via the default
        # factory surfaces FreecadNotAvailableError (graceful degradation).
        import tool_registry.tools.freecad.operations as ops
        from tool_registry.tools.freecad.operations import FreecadNotAvailableError

        monkeypatch.setattr(ops, "HAS_FREECAD", False)
        store = FreecadSessionStore()  # default factory
        if ops.HAS_FREECAD:  # pragma: no cover - only on a FreeCAD host
            pytest.skip("FreeCAD installed; degradation path not exercised")
        with pytest.raises(FreecadNotAvailableError):
            store.open_session()

    def test_ttl_default_is_sane(self) -> None:
        assert DEFAULT_TTL_SECONDS == 1800
