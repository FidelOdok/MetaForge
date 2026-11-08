"""The isolated CadQuery script runner, for real: each case starts the child.

FORGE-830. Every escape a script can attempt must fail safely with a clear
error, and leave nothing behind. These run wherever Linux has Landlock (CI's
ubuntu runners do) and skip elsewhere. They need no CadQuery: each attack runs
before the result would be exported. The one case that builds geometry skips
without CadQuery; the hardened container covers it
(``test_cadquery_isolation_live.py``), together with the uid switch, which
needs the capabilities only that container has.

Here the runner runs as the test's own uid, so what stops each attack is
Landlock, seccomp and the resource limits, not file ownership.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the isolated runner needs Linux"
)

if sys.platform.startswith("linux"):
    from tool_registry.tools.cadquery.script_child import landlock_abi
    from tool_registry.tools.cadquery.script_runner import (
        RunnerLimits,
        ScriptLimitError,
        ScriptRunError,
        ScriptRunner,
        ScriptRunTimeoutError,
    )

    if landlock_abi() < 1:
        pytestmark = pytest.mark.skip(reason="this kernel has no Landlock")

REPO_ROOT = Path(__file__).resolve().parents[2]

# Reaches the real builtins (``open``, ``__import__``) through dunder
# attributes, which the source checks do not stop. Every attack starts here,
# so each one tests the OS layers, not the blocked-name list.
ESCAPE = (
    "B = [c for c in ().__class__.__base__.__subclasses__() "
    "if c.__name__ == 'catch_warnings'][0]()._module.__builtins__\n"
    "imp = B['__import__']\n"
)


@pytest.fixture
def runner(tmp_path: Path) -> ScriptRunner:
    return ScriptRunner(
        root=str(tmp_path / "runner"),
        require=frozenset({"landlock", "seccomp"}),
        limits=RunnerLimits(
            memory_bytes=2048 * 1024 * 1024,
            file_bytes=8 * 1024 * 1024,
            output_bytes=4 * 1024 * 1024,
            startup_grace_s=20.0,
        ),
    )


def _no_child_left() -> None:
    """No script child is still running, and no private folder is left."""
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        assert b"script_child.py" not in cmdline, f"script child {pid} is still running"


def test_the_dunder_escape_reaches_os_but_os_is_confined(runner, tmp_path):
    """The source checks are bypassed (the escape works), and the OS still
    refuses: no reading outside the allowed paths, no running a program."""
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "key.txt").write_text("s3cret")
    script = ESCAPE + f"os = imp('os')\nprint(os.listdir({str(secret)!r}))"
    with pytest.raises(ScriptRunError, match="Permission denied"):
        runner.run(script, ".step", 10)
    with pytest.raises(ScriptRunError, match="not permitted"):
        runner.run(ESCAPE + "imp('subprocess').run(['touch', '/tmp/pwned-by-script'])", ".step", 10)
    assert not Path("/tmp/pwned-by-script").exists()  # noqa: S108
    _no_child_left()


@pytest.mark.parametrize("where", ["workspace", "app"])
def test_writing_outside_the_private_folder_is_refused(runner, tmp_path, where):
    """``/workspace`` stands in as a folder the test user can write; ``/app``
    is the adapter's own code (the checkout here)."""
    if where == "workspace":
        target_dir = tmp_path / "workspace"
        target_dir.mkdir()
    else:
        target_dir = REPO_ROOT / "tool_registry"
    target = target_dir / "pwned-by-script.txt"
    script = ESCAPE + f"B['open']({str(target)!r}, 'w').write('x')"
    # A checkout the test can write: Landlock refuses ("Permission denied").
    # Mounted read-only (CI runs these inside the image too): EROFS comes first.
    refused = "Permission denied" if os.access(target_dir, os.W_OK) else "Read-only file system"
    with pytest.raises(ScriptRunError, match=refused):
        runner.run(script, ".step", 10)
    assert not target.exists()
    if importlib.util.find_spec("cadquery") is not None:
        # The CadQuery exporters the namespace hands every script are no way out either.
        exported = target_dir / "pwned-by-exporter.step"
        script = (
            f"result = cq.Workplane().box(1, 1, 1)\nexporters.export(result, {str(exported)!r})"
        )
        try:
            runner.run(script, ".step", 30)
        except ScriptRunError:
            pass  # refused loudly or quietly; either way nothing is written
        assert not exported.exists()
    _no_child_left()


def test_opening_a_network_socket_is_refused(runner):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    try:
        script = (
            ESCAPE + f"s = imp('socket').socket()\ns.connect(('127.0.0.1', {port}))\n"
            "s.sendall(b'escaped')"
        )
        with pytest.raises(ScriptRunError, match="Permission denied"):
            runner.run(script, ".step", 10)
        with pytest.raises(OSError):  # nothing ever connected
            listener.accept()
    finally:
        listener.close()
    _no_child_left()


def test_a_fork_bomb_cannot_start_a_process(runner):
    script = ESCAPE + "os = imp('os')\nwhile True:\n    os.fork()"
    started = time.monotonic()
    with pytest.raises(ScriptRunError, match="Operation not permitted"):
        runner.run(script, ".step", 10)
    assert time.monotonic() - started < 30
    _no_child_left()


def test_an_infinite_loop_is_killed_at_its_deadline(runner):
    runner.limits = RunnerLimits(startup_grace_s=5.0)
    started = time.monotonic()
    with pytest.raises(ScriptRunTimeoutError, match="killed"):
        runner.run("while True:\n    pass", ".step", 2)
    elapsed = time.monotonic() - started
    assert 2 <= elapsed < 20
    _no_child_left()
    assert os.listdir(runner.root) == []


def test_a_sleeping_script_is_killed_at_its_deadline(runner):
    runner.limits = RunnerLimits(startup_grace_s=5.0)
    with pytest.raises(ScriptRunTimeoutError):
        runner.run(ESCAPE + "imp('time').sleep(600)", ".step", 1)
    _no_child_left()


def test_a_memory_hog_hits_the_memory_limit(runner):
    script = ESCAPE + "x = B['bytearray'](8 * 1024 * 1024 * 1024)"
    with pytest.raises(ScriptLimitError, match="memory limit") as exc:
        runner.run(script, ".step", 20)
    assert exc.value.limit == "memory"
    _no_child_left()


def test_a_growing_memory_hog_is_stopped(runner):
    script = ESCAPE + "x = []\nwhile True:\n    x.append(B['bytearray'](64 * 1024 * 1024))"
    with pytest.raises(ScriptLimitError) as exc:
        runner.run(script, ".step", 20)
    assert exc.value.limit in {"memory", "cpu"}
    _no_child_left()


def test_huge_printed_output_hits_the_file_size_limit(runner):
    script = "while True:\n    print('x' * 100000)"
    with pytest.raises(ScriptLimitError, match="file size limit"):
        runner.run(script, ".step", 20)
    _no_child_left()
    assert os.listdir(runner.root) == []


def test_a_huge_file_in_the_private_folder_hits_the_file_size_limit(runner):
    script = ESCAPE + "f = B['open']('big.bin', 'wb')\nwhile True:\n    f.write(b'x' * 1000000)"
    with pytest.raises(ScriptLimitError, match="file size limit"):
        runner.run(script, ".step", 20)
    _no_child_left()


def test_flooding_the_result_channel_is_cut_off(runner):
    """Writing straight to the descriptor the result travels on, past the
    output cap: the adapter stops reading and kills the run."""
    script = (
        ESCAPE + "os = imp('os')\nfor fd in range(3, 12):\n    try:\n"
        "        while True:\n            os.write(fd, b'x' * 65536)\n"
        "    except OSError:\n        pass\n"
    )
    with pytest.raises((ScriptLimitError, ScriptRunError)) as exc:
        runner.run(script, ".step", 20)
    if isinstance(exc.value, ScriptLimitError):
        assert exc.value.limit in {"output", "file_size"}
    _no_child_left()


def test_the_adapter_environment_does_not_reach_the_script(runner, monkeypatch):
    monkeypatch.setenv("METAFORGE_TEST_SECRET", "do-not-leak")
    script = ESCAPE + "raise ValueError(sorted(imp('os').environ))"
    with pytest.raises(ScriptRunError) as exc:
        runner.run(script, ".step", 10)
    assert "METAFORGE_TEST_SECRET" not in str(exc.value)
    assert "HOME" in str(exc.value)


def test_a_normal_script_returns_a_step(runner):
    pytest.importorskip("cadquery")
    run = runner.run("result = cq.Workplane('XY').box(10, 10, 10)", ".step", 60)
    assert run.data.startswith(b"ISO-10303-21")
    assert run.properties["volume_mm3"] == pytest.approx(1000.0)
    assert run.isolation["landlock_abi"] >= 1
    assert run.isolation["seccomp"] is True
    _no_child_left()


def test_execute_script_runs_isolated_and_writes_its_result(tmp_path):
    """The tool end to end, in its default mode."""
    pytest.importorskip("cadquery")
    from tool_registry.tools.cadquery.operations import CadqueryOperations

    ops = CadqueryOperations(
        work_dir=str(tmp_path / "work"),
        script_runner=ScriptRunner(root=str(tmp_path / "runner")),
    )
    assert ops.script_isolation == "process"
    result = ops.execute_script("import cadquery as cq\nresult = cq.Workplane().box(2, 3, 4)")
    assert Path(result["cad_file"]).read_bytes().startswith(b"ISO-10303-21")
    assert result["volume_mm3"] == pytest.approx(24.0)
    assert result["step_base64"]
