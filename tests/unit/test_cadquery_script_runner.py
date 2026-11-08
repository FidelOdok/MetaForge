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
import sys
import types

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


GB = 1024 * 1024 * 1024
LIMITS = {
    "memory_bytes": 2 * GB,
    "address_space_bytes": 16 * GB,
    "cpu_seconds": 60,
    "file_bytes": 64,
    "open_files": 256,
    "processes": 256,
}
_RLIMITS = {
    "RLIMIT_AS": 9,
    "RLIMIT_DATA": 2,
    "RLIMIT_CPU": 0,
    "RLIMIT_FSIZE": 1,
    "RLIMIT_NOFILE": 7,
    "RLIMIT_CORE": 4,
    "RLIMIT_NPROC": 6,
}


@pytest.fixture
def fake_resource(monkeypatch):
    """A stand-in ``resource`` module; returns the ``(name, value)`` set, in order."""
    calls: list[tuple[str, int]] = []
    by_number = {v: k for k, v in _RLIMITS.items()}
    fake = types.ModuleType("resource")
    fake.__dict__.update(_RLIMITS)
    fake.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    fake.getrlimit = lambda which: (-1, -1)  # type: ignore[attr-defined]
    fake.setrlimit = lambda which, pair: calls.append((by_number[which], pair[1]))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake)
    return calls


class TestMemoryLimits:
    """FORGE-832: the budget bounds real memory, not address space."""

    def test_the_budget_goes_on_data_under_an_address_space_backstop(self):
        assert script_child.memory_rlimits(2 * GB, 16 * GB, True) == [
            ("RLIMIT_AS", 16 * GB),
            ("RLIMIT_DATA", 2 * GB),
        ]

    def test_without_mmap_accounting_the_budget_falls_back_to_address_space(self):
        assert script_child.memory_rlimits(2 * GB, 16 * GB, False) == [("RLIMIT_AS", 2 * GB)]

    def test_the_backstop_is_never_below_the_budget(self):
        assert script_child.memory_rlimits(4 * GB, GB, True)[0] == ("RLIMIT_AS", 4 * GB)

    def test_data_is_used_when_the_kernel_counts_mmap(self, fake_resource, monkeypatch):
        monkeypatch.setattr(script_child, "_data_limit_covers_mmap", lambda budget: True)
        assert script_child._set_limits(LIMITS, own_uid=False) == "data"
        # The last value set for each limit is the one that holds.
        final = dict(fake_resource)
        assert final["RLIMIT_AS"] == 16 * GB
        assert final["RLIMIT_DATA"] == 2 * GB
        assert "RLIMIT_NPROC" not in final

    def test_otherwise_the_budget_falls_back_to_address_space(self, fake_resource, monkeypatch):
        monkeypatch.setattr(script_child, "_data_limit_covers_mmap", lambda budget: False)
        assert script_child._set_limits(LIMITS, own_uid=True) == "address_space"
        final = dict(fake_resource)
        assert final["RLIMIT_AS"] == 2 * GB
        assert final["RLIMIT_NPROC"] == 256

    def test_an_inherited_hard_limit_is_never_raised(self, fake_resource, monkeypatch):
        sys.modules["resource"].getrlimit = lambda which: (-1, 8 * GB if which == 9 else -1)  # type: ignore[attr-defined]
        monkeypatch.setattr(script_child, "_data_limit_covers_mmap", lambda budget: True)
        script_child._set_limits(LIMITS, own_uid=False)
        assert dict(fake_resource)["RLIMIT_AS"] == 8 * GB

    def test_an_occt_out_of_memory_is_a_memory_limit(self):
        class Standard_OutOfMemory(Exception):  # noqa: N801  (OCP's name)
            pass

        assert script_child._classify(Standard_OutOfMemory()) == "memory_limit"
        assert script_child._classify(MemoryError()) == "memory_limit"
        assert script_child._classify(ValueError()) == "script_error"

    def test_a_thread_that_cannot_get_memory_is_a_memory_limit(self, tmp_path):
        with pytest.raises(ScriptLimitError, match="2048 MB memory limit") as exc:
            _runner(tmp_path)._interpret(127, b"", b"", False, False, ".step", 1.0)
        assert exc.value.limit == "memory"


class TestChildEnvironment:
    def test_arenas_and_math_threads_are_capped(self, tmp_path):
        env = _runner(tmp_path)._env("/run/x")
        assert env["MALLOC_ARENA_MAX"] == "2"
        assert env["OMP_NUM_THREADS"] == "1"
        assert env["OPENBLAS_NUM_THREADS"] == "1"

    def test_nothing_of_the_adapter_reaches_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METAFORGE_TEST_SECRET", "x")
        env = _runner(tmp_path)._env("/run/x")
        assert "METAFORGE_TEST_SECRET" not in env
        assert env["HOME"] == env["TMPDIR"] == "/run/x"


class TestMemoryConfiguration:
    def test_defaults(self, monkeypatch):
        for name in (
            "CADQUERY_RUNNER_MEMORY_MB",
            "CADQUERY_RUNNER_ADDRESS_SPACE_MB",
            "CADQUERY_RUNNER_MAX_CONCURRENT",
        ):
            monkeypatch.delenv(name, raising=False)
        runner = ScriptRunner.from_env()
        assert runner.limits.memory_bytes == 2048 * 1024 * 1024
        assert runner.limits.address_space_bytes == 16 * GB
        assert runner.max_concurrent == 2

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("CADQUERY_RUNNER_MEMORY_MB", "3072")
        monkeypatch.setenv("CADQUERY_RUNNER_ADDRESS_SPACE_MB", "8192")
        monkeypatch.setenv("CADQUERY_RUNNER_MAX_CONCURRENT", "3")
        runner = ScriptRunner.from_env()
        assert runner.limits.memory_bytes == 3072 * 1024 * 1024
        assert runner.limits.address_space_bytes == 8 * GB
        assert runner.max_concurrent == 3

    @pytest.mark.parametrize(("pool", "max_concurrent", "slots"), [(4, 2, 2), (2, 4, 2), (0, 3, 3)])
    def test_concurrency_is_the_smaller_of_pool_and_setting(
        self, tmp_path, pool, max_concurrent, slots
    ):
        runner = _runner(
            tmp_path, uid_pool=list(range(61000, 61000 + pool)), max_concurrent=max_concurrent
        )
        taken = 0
        while runner._slots.acquire(blocking=False):
            taken += 1
        assert taken == slots
