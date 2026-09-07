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
    assert stash.fill(args)
    assert args["step_base64"] == "U1RFUA=="


def test_remember_returns_false_on_miss() -> None:
    """MET-642 S4 finding: remember()'s return value is what lets both
    dispatch seams log a miss instead of it silently vanishing into a
    generic "no geometry to commit" error with no diagnosable cause."""
    stash = GeometryStash()
    assert stash.remember({"session_id": "s1"}, {"step_base64": "X"}) is False  # no obj_id
    assert stash.remember({"session_id": "s1", "obj_id": "o1"}, {}) is False  # no blob
    assert stash.remember({"session_id": "s1", "obj_id": "o1"}, "not-a-dict") is False  # type: ignore[arg-type]


def test_a_stash_hit_wins_over_a_reproduced_blob() -> None:
    """MET-684 reversed this precedence, and this test with it.

    It used to assert "explicit always wins" -- the behaviour that let a
    damaged reproduction silently replace the pristine export. A caller that
    names a (session_id, obj_id) the stash knows is committing *that* export;
    whatever blob it carried back is a copy, and a copy can only be equal or
    wrong.
    """
    stash = GeometryStash()
    stash.remember({"session_id": "s1", "obj_id": "o1"}, {"step_base64": "CACHED"})
    args = {"session_id": "s1", "obj_id": "o1", "step_base64": "DAMAGED"}

    result = stash.fill(args)

    assert args["step_base64"] == "CACHED"
    assert result.injected is True
    assert result.diverged is True


def test_an_explicit_blob_survives_a_stash_miss() -> None:
    """The other half of the precedence change: geometry authored some other
    way (a raw CadQuery script, an upload) has no stash entry, and must still
    commit exactly what the caller supplied."""
    stash = GeometryStash()
    args = {"session_id": "s1", "obj_id": "never-exported", "step_base64": "ONLY-COPY"}

    result = stash.fill(args)

    assert args["step_base64"] == "ONLY-COPY"
    assert result.injected is False
    assert result.diverged is False


def test_a_faithful_reproduction_is_not_reported_as_divergence() -> None:
    """An agent that threads the blob back correctly is doing nothing wrong --
    it must not generate a warning."""
    stash = GeometryStash()
    stash.remember({"session_id": "s1", "obj_id": "o1"}, {"step_base64": "SAME"})
    args = {"session_id": "s1", "obj_id": "o1", "step_base64": "SAME"}

    result = stash.fill(args)

    assert args["step_base64"] == "SAME"
    assert result.diverged is False


def test_the_live_caught_single_character_corruption_is_repaired() -> None:
    """MET-684's actual artefact.

    The committed STEP contained ``NAMED_URIT(*)`` where the fixed OCCT
    boilerplate reads ``NAMED_UNIT(*)``; OCCT crashed parsing it
    ("Incorrect Syntax : Fails Count: 3") and the viewer got a 500. One wrong
    base64 character reproduces exactly that one-byte change with both
    neighbouring bytes intact, which is why the damage is attributable to the
    blob being carried as text between two tool calls.
    """
    import base64

    pristine = b"#20=(NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));\n"
    damaged = pristine.replace(b"NAMED_UNIT", b"NAMED_URIT")
    assert len(damaged) == len(pristine)  # substitution, not truncation

    stash = GeometryStash()
    stash.remember(
        {"session_id": "s1", "obj_id": "o1"},
        {"step_base64": base64.b64encode(pristine).decode("ascii")},
    )
    args = {
        "session_id": "s1",
        "obj_id": "o1",
        "step_base64": base64.b64encode(damaged).decode("ascii"),
    }

    result = stash.fill(args)

    assert base64.b64decode(args["step_base64"]) == pristine
    assert result.diverged is True


def test_miss_leaves_args_untouched() -> None:
    stash = GeometryStash()
    args = {"session_id": "s1", "obj_id": "never-exported", "name": "x"}
    assert not stash.fill(args)
    assert "step_base64" not in args


def test_remember_ignores_incomplete_calls() -> None:
    stash = GeometryStash()
    stash.remember({"session_id": "s1"}, {"step_base64": "X"})  # no obj_id
    stash.remember({"session_id": "s1", "obj_id": "o1"}, {"size_bytes": 3})  # no blob
    assert not stash.fill({"session_id": "s1", "obj_id": "o1"})


def test_lru_eviction() -> None:
    stash = GeometryStash(max_entries=2)
    for i in range(3):
        stash.remember({"session_id": "s", "obj_id": f"o{i}"}, {"step_base64": f"b{i}"})
    # o0 evicted; o1 and o2 remain
    assert not stash.fill({"session_id": "s", "obj_id": "o0"})
    a = {"session_id": "s", "obj_id": "o2"}
    assert stash.fill(a) and a["step_base64"] == "b2"
