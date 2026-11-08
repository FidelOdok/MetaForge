"""The isolated CadQuery script runner, without starting a child (FORGE-830).

The child itself is exercised for real in
``tests/integration/test_cadquery_script_runner.py`` (Linux) and against the
hardened container in ``tests/integration/test_cadquery_isolation_live.py``.
"""

from __future__ import annotations

import base64
import json
import signal
import struct

import pytest

from tool_registry.tools.cadquery import script_child
from tool_registry.tools.cadquery.script_runner import (
    RunnerLimits,
    ScriptIsolationError,
    ScriptLimitError,
    ScriptRunError,
    ScriptRunner,
    ScriptRunTimeoutError,
    _clean_properties,
    _parse_uid_pool,
)

STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
PROPS = {
    "volume_mm3": 1000.0,
    "surface_area_mm2": 600.0,
    "bounding_box": {
        "min_x": -5.0,
        "min_y": -5.0,
        "min_z": -5.0,
        "max_x": 5.0,
        "max_y": 5.0,
        "max_z": 5.0,
    },
}


def _runner(tmp_path, **kwargs) -> ScriptRunner:
    return ScriptRunner(root=str(tmp_path / "runner"), **kwargs)


def _ok(data: bytes = STEP, props: object = PROPS) -> bytes:
    return json.dumps(
        {"ok": True, "output_b64": base64.b64encode(data).decode(), "properties": props}
    ).encode()


class TestUidPool:
    def test_ranges_and_lists(self):
        assert _parse_uid_pool("61000-61002, 61010") == [61000, 61001, 61002, 61010]
        assert _parse_uid_pool("") == []

    def test_root_is_never_in_the_pool(self):
        with pytest.raises(ValueError, match="uid 0"):
            _parse_uid_pool("0-3")


class TestConfiguration:
    def test_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CADQUERY_RUNNER_ROOT", str(tmp_path))
        monkeypatch.setenv("CADQUERY_RUNNER_UIDS", "61000-61001")
        monkeypatch.delenv("CADQUERY_RUNNER_REQUIRE", raising=False)
        monkeypatch.setenv("CADQUERY_RUNNER_MEMORY_MB", "1024")
        runner = ScriptRunner.from_env()
        assert runner.root == str(tmp_path)
        assert runner.uid_pool == [61000, 61001]
        # A uid pool makes the uid switch a requirement by default.
        assert runner.require == {"uid", "landlock", "seccomp"}
        assert runner.limits.memory_bytes == 1024 * 1024 * 1024

    def test_without_a_pool_landlock_and_seccomp_are_still_required(self, monkeypatch):
        monkeypatch.delenv("CADQUERY_RUNNER_UIDS", raising=False)
        monkeypatch.delenv("CADQUERY_RUNNER_REQUIRE", raising=False)
        assert ScriptRunner.from_env().require == {"landlock", "seccomp"}

    def test_unknown_layer_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown isolation layer"):
            _runner(tmp_path, require=frozenset({"chroot"}))

    def test_uid_required_without_a_pool_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        runner = _runner(tmp_path, require=frozenset({"uid"}))
        with pytest.raises(ScriptIsolationError, match="no uid pool"):
            runner.run("result = 1", ".step", 5)

    def test_unsupported_output_format(self, tmp_path):
        with pytest.raises(ValueError, match="unsupported output format"):
            _runner(tmp_path).run("result = 1", ".exe", 5)


class TestInterpret:
    """How a finished child's exit and output become a result or an error."""

    def test_ok(self, tmp_path):
        run = _runner(tmp_path)._interpret(0, _ok(), b"", False, False, ".step", 1.0)
        assert run.data == STEP
        assert run.properties == PROPS

    def test_timeout(self, tmp_path):
        with pytest.raises(ScriptRunTimeoutError):
            _runner(tmp_path)._interpret(-9, b"", b"", False, True, ".step", 9.0)

    def test_output_overflow(self, tmp_path):
        with pytest.raises(ScriptLimitError, match="output exceeded") as exc:
            _runner(tmp_path)._interpret(-9, b"x", b"", True, False, ".step", 1.0)
        assert exc.value.limit == "output"

    @pytest.mark.parametrize(
        ("kind", "limit"), [("memory_limit", "memory"), ("file_size_limit", "file_size")]
    )
    def test_limits_reported_by_the_child(self, tmp_path, kind, limit):
        out = json.dumps({"ok": False, "kind": kind, "error": "x"}).encode()
        with pytest.raises(ScriptLimitError) as exc:
            _runner(tmp_path)._interpret(0, out, b"", False, False, ".step", 1.0)
        assert exc.value.limit == limit

    def test_isolation_refusal(self, tmp_path):
        out = json.dumps(
            {"ok": False, "kind": "isolation_unavailable", "error": "Landlock: missing"}
        ).encode()
        with pytest.raises(ScriptIsolationError, match="refused to run: Landlock"):
            _runner(tmp_path)._interpret(0, out, b"", False, False, ".step", 1.0)

    @pytest.mark.skipif(not hasattr(signal, "SIGXCPU"), reason="POSIX signals")
    @pytest.mark.parametrize("sig", ["SIGKILL", "SIGXCPU"])
    def test_killed_by_a_signal_is_a_cpu_or_memory_limit(self, tmp_path, sig):
        with pytest.raises(ScriptLimitError) as exc:
            _runner(tmp_path)._interpret(
                -int(getattr(signal, sig)), b"", b"", False, False, ".step", 1.0
            )
        assert exc.value.limit == "cpu"

    def test_no_result_at_all(self, tmp_path):
        with pytest.raises(ScriptRunError, match="returned no result"):
            _runner(tmp_path)._interpret(3, b"", b"boom", False, False, ".step", 1.0)

    def test_a_step_that_is_not_a_step_is_refused(self, tmp_path):
        with pytest.raises(ScriptRunError, match="not a STEP file"):
            _runner(tmp_path)._interpret(0, _ok(b"#!/bin/sh"), b"", False, False, ".step", 1.0)

    def test_other_formats_are_not_header_checked(self, tmp_path):
        run = _runner(tmp_path)._interpret(0, _ok(b"solid x"), b"", False, False, ".stl", 1.0)
        assert run.data == b"solid x"

    def test_too_large_after_decoding(self, tmp_path):
        runner = _runner(tmp_path, limits=RunnerLimits(output_bytes=4))
        with pytest.raises(ScriptLimitError):
            runner._interpret(0, _ok(), b"", False, False, ".step", 1.0)

    def test_bad_base64(self, tmp_path):
        out = json.dumps({"ok": True, "output_b64": "@@@", "properties": PROPS}).encode()
        with pytest.raises(ScriptRunError, match="bad output"):
            _runner(tmp_path)._interpret(0, out, b"", False, False, ".step", 1.0)


class TestProperties:
    """The child's numbers are accepted only in the expected shape."""

    def test_clean(self):
        assert _clean_properties(PROPS) == PROPS

    def test_empty_shape_has_no_bounding_box(self):
        props = {"volume_mm3": 0.0, "surface_area_mm2": 0.0, "bounding_box": None}
        assert _clean_properties(props)["bounding_box"] is None

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            {"volume_mm3": "1", "surface_area_mm2": 1.0},
            {"volume_mm3": True, "surface_area_mm2": 1.0},
            {"volume_mm3": 1.0, "surface_area_mm2": 1.0, "bounding_box": {"min_x": "x"}},
        ],
    )
    def test_bad(self, bad):
        with pytest.raises(ScriptRunError):
            _clean_properties(bad)

    def test_extra_keys_are_dropped(self):
        assert set(_clean_properties({**PROPS, "path": "/etc/passwd"})) == set(PROPS)


class TestSeccompProgram:
    @pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
    def test_builds_and_ends_by_allowing(self, machine):
        program = script_child._seccomp_program(machine)
        assert len(program) % 8 == 0
        insns = [struct.unpack("=HBBI", program[i : i + 8]) for i in range(0, len(program), 8)]
        # First the architecture check, last the default allow.
        assert insns[0] == (0x20, 0, 0, 4)
        assert insns[-1] == (0x06, 0, 0, 0x7FFF0000)
        # Every jump lands inside the program.
        for i, (code, jt, jf, _k) in enumerate(insns):
            if code & 0x07 == 0x05:
                assert i + 1 + max(jt, jf) < len(insns)

    def test_names_the_socket_and_clone_checks(self):
        program = script_child._seccomp_program("x86_64")
        ks = {struct.unpack("=HBBI", program[i : i + 8])[3] for i in range(0, len(program), 8)}
        assert {41, 56, 57, 58, 59, 435} <= ks  # socket, clone, fork, vfork, execve, clone3
