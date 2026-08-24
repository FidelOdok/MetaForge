"""Commit-by-reference geometry stash (MET-10)."""

from __future__ import annotations

from metaforge.mcp.server import GeometryStash


def test_remember_then_fill_by_reference() -> None:
    stash = GeometryStash()
    remembered = stash.remember(
        {"session_id": "s1", "obj_id": "assembly_4"},
        {"step_base64": "U1RFUA==", "size_bytes": 8},
    )
    assert remembered is True
    args = {"session_id": "s1", "obj_id": "assembly_4", "name": "Gimbal Base"}
    assert stash.fill(args) is True
    assert args["step_base64"] == "U1RFUA=="


def test_remember_returns_false_on_miss() -> None:
    """MET-642 S4 finding: remember()'s return value is what lets both
    dispatch seams log a miss instead of it silently vanishing into a
    generic "no geometry to commit" error with no diagnosable cause."""
    stash = GeometryStash()
    assert stash.remember({"session_id": "s1"}, {"step_base64": "X"}) is False  # no obj_id
    assert stash.remember({"session_id": "s1", "obj_id": "o1"}, {}) is False  # no blob
    assert stash.remember({"session_id": "s1", "obj_id": "o1"}, "not-a-dict") is False  # type: ignore[arg-type]


def test_explicit_blob_wins() -> None:
    stash = GeometryStash()
    stash.remember({"session_id": "s1", "obj_id": "o1"}, {"step_base64": "CACHED"})
    args = {"session_id": "s1", "obj_id": "o1", "step_base64": "EXPLICIT"}
    assert stash.fill(args) is False
    assert args["step_base64"] == "EXPLICIT"


def test_miss_leaves_args_untouched() -> None:
    stash = GeometryStash()
    args = {"session_id": "s1", "obj_id": "never-exported", "name": "x"}
    assert stash.fill(args) is False
    assert "step_base64" not in args


def test_remember_ignores_incomplete_calls() -> None:
    stash = GeometryStash()
    stash.remember({"session_id": "s1"}, {"step_base64": "X"})  # no obj_id
    stash.remember({"session_id": "s1", "obj_id": "o1"}, {"size_bytes": 3})  # no blob
    assert stash.fill({"session_id": "s1", "obj_id": "o1"}) is False


def test_lru_eviction() -> None:
    stash = GeometryStash(max_entries=2)
    for i in range(3):
        stash.remember({"session_id": "s", "obj_id": f"o{i}"}, {"step_base64": f"b{i}"})
    # o0 evicted; o1 and o2 remain
    assert stash.fill({"session_id": "s", "obj_id": "o0"}) is False
    a = {"session_id": "s", "obj_id": "o2"}
    assert stash.fill(a) and a["step_base64"] == "b2"
