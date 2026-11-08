"""cadquery.execute_script against the hardened cadquery-adapter container.

FORGE-830. Runs only when ``CADQUERY_ISOLATION_LIVE_URL`` names a running
adapter's ``/mcp`` endpoint, started from ``docker-compose.yml`` (CI's
``cadquery-isolation`` job does exactly this). ``CADQUERY_ISOLATION_LIVE_EXEC``
is the command prefix that runs a command inside that container, for checking
what a script could not do::

    docker compose up -d cadquery-adapter
    CADQUERY_ISOLATION_LIVE_URL=http://localhost:8101/mcp \\
    CADQUERY_ISOLATION_LIVE_EXEC="docker compose exec -T cadquery-adapter" \\
        pytest tests/integration/test_cadquery_isolation_live.py

Unlike ``test_cadquery_script_runner.py`` this covers the whole production
path: the compose hardening (read-only root, dropped capabilities,
no-new-privileges), the image's start-up (uid 1000 keeping only CAP_SETUID and
CAP_SETGID) and the runner's uid switch.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import uuid
from typing import Any

import pytest

URL = os.environ.get("CADQUERY_ISOLATION_LIVE_URL", "")
EXEC = shlex.split(os.environ.get("CADQUERY_ISOLATION_LIVE_EXEC", ""))

pytestmark = pytest.mark.skipif(
    not (URL and EXEC), reason="needs a running hardened cadquery-adapter (see module docstring)"
)

# The adapter's source checks refuse the words __import__, open, os, ... so
# each attack spells them in pieces: what has to stop it is the OS.
ESCAPE = (
    "B = [c for c in ().__class__.__base__.__subclasses__() "
    "if c.__name__ == 'catch_warnings'][0]()._module.__builtins__\n"
    "imp = B['__imp' + 'ort__']\n"
    "opn = B['op' + 'en']\n"
    "o = imp('o' + 's')\n"
)
RUNNER_UIDS = {str(u) for u in range(61000, 61004)}


def _script(script: str, timeout: float = 60, **extra: Any) -> tuple[dict[str, Any], str]:
    """Call execute_script; return (data, error message)."""
    import httpx

    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "tool/call",
        "params": {
            "tool_id": "cadquery.execute_script",
            "arguments": {"script": script, "timeout": timeout, **extra},
        },
    }
    response = httpx.post(URL, json=body, timeout=timeout + 60).json()
    if "error" in response:
        err = response["error"]
        return {}, f"{err.get('message', '')} {json.dumps(err.get('data', ''))}"
    return response["result"]["data"], ""


def _in_container(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [*EXEC, *command], capture_output=True, text=True, timeout=60, check=False
    )


def _exists(path: str) -> bool:
    return _in_container("test", "-e", path).returncode == 0


def _runner_processes() -> list[str]:
    """uids of every process in the container; a runner uid means a leftover child."""
    out = _in_container("sh", "-c", "for p in /proc/[0-9]*; do stat -c %u $p; done 2>/dev/null")
    return [u for u in out.stdout.split() if u in RUNNER_UIDS]


def test_the_adapter_runs_as_uid_1000_with_only_setuid_and_setgid() -> None:
    status = _in_container("cat", "/proc/1/status").stdout
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    assert fields["Uid"].split()[0] == "1000"
    # CAP_SETGID (6) and CAP_SETUID (7) only.
    assert int(fields["CapEff"].strip(), 16) == 0xC0
    assert fields["NoNewPrivs"].strip() == "1"


def test_the_root_file_system_is_read_only() -> None:
    assert _in_container("touch", "/etc/pwned").returncode != 0
    assert not _exists("/etc/pwned")


def test_a_normal_script_returns_a_step() -> None:
    data, error = _script("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)")
    assert not error, error
    assert data["volume_mm3"] == pytest.approx(1000.0)
    assert base64.b64decode(data["step_base64"]).startswith(b"ISO-10303-21")
    assert data["cad_file"].startswith("/workspace/output/cadquery/script/")
    assert _exists(data["cad_file"])


def test_the_script_runs_as_a_runner_uid_not_the_adapter() -> None:
    _, error = _script(ESCAPE + "raise ValueError('uid=%d' % o.getuid())")
    assert any(f"uid={u}" in error for u in RUNNER_UIDS), error


def test_the_script_cannot_signal_or_inspect_the_adapter() -> None:
    _, error = _script(ESCAPE + "o.kill(1, 9)")
    assert "PermissionError: [Errno 1] Operation not permitted" in error, error
    _, error = _script(ESCAPE + "print(opn('/proc/1/environ').read())")
    assert "Permission denied: '/proc/1/environ'" in error, error


def test_a_thread_bomb_stops_at_the_process_limit() -> None:
    _, error = _script(
        ESCAPE + "th = imp('threading')\nfor i in range(2000):\n"
        "    th.Thread(target=imp('time').sleep, args=(60,)).start()",
        timeout=30,
    )
    assert "can't start new thread" in error, error
    assert _runner_processes() == []


@pytest.mark.parametrize("target", ["/workspace/pwned-by-script.txt", "/app/pwned-by-script.txt"])
def test_writing_workspace_or_app_is_refused(target: str) -> None:
    _, error = _script(ESCAPE + f"opn({target!r}, 'w').write('x')")
    assert target in error, error
    assert "Permission denied" in error or "Read-only file system" in error, error
    assert not _exists(target)


def test_the_exporters_cannot_write_the_workspace() -> None:
    target = "/workspace/pwned-by-exporter.step"
    _script(f"result = cq.Workplane().box(1, 1, 1)\nexporters.export(result, {target!r})")
    assert not _exists(target)


def test_the_workspace_cannot_even_be_read() -> None:
    _, error = _script(ESCAPE + "print(o.listdir('/workspace'))")
    assert "Permission denied: '/workspace'" in error, error


def test_a_socket_to_the_adapter_itself_is_refused() -> None:
    """The adapter listens on 8101, so a refusal here is the runner's doing."""
    _, error = _script(ESCAPE + "imp('socket').create_connection(('127.0.0.1', 8101))")
    assert "PermissionError: [Errno 13] Permission denied" in error, error


def test_a_fork_bomb_cannot_start_a_process() -> None:
    _, error = _script(ESCAPE + "while True:\n    o.fork()")
    assert "PermissionError: [Errno 1] Operation not permitted" in error, error
    assert _runner_processes() == []


def test_an_infinite_loop_is_killed_and_leaves_nothing_running() -> None:
    _, error = _script("while True:\n    pass", timeout=3)
    assert "timeout" in error, error
    assert _runner_processes() == []
    assert _in_container("ls", "-A", "/run/cadquery-runner").stdout.strip() == ""


def test_a_memory_hog_hits_the_memory_limit() -> None:
    _, error = _script(ESCAPE + "x = B['bytearray'](8 * 1024 * 1024 * 1024)")
    assert "memory limit" in error, error


def test_a_heavy_part_on_a_16_thread_pool_returns_a_step() -> None:
    """FORGE-832: a desk-lamp base, arm and head (fillets, shell, 77 holes, a
    loft, unions) with OCCT's pool sized as on a 16-CPU host. Under the old 2 GB
    address-space limit this failed with "memory limit" while using 0.67 GB."""
    script = ESCAPE + (
        "imp('OCP.OSD', fromlist=['OSD_ThreadPool']).OSD_ThreadPool.DefaultPool_s(16)\n"
        "base = (cq.Workplane('XY').box(130, 90, 47).edges('|Z').fillet(8)\n"
        "        .faces('>Z').shell(-3).faces('<Z').workplane().rarray(10, 10, 11, 7).hole(3))\n"
        "loft = cq.Workplane('XY').rect(40, 30).workplane(offset=60).circle(12).loft()\n"
        "head = (cq.Workplane('XY').cylinder(30, 35).faces('>Z').workplane()\n"
        "        .rarray(5, 1, 12, 1).rect(1.5, 50).extrude(15))\n"
        "result = base.union(loft.translate((0, 0, 60))).union(head.translate((0, 0, 140)))\n"
    )
    data, error = _script(script, timeout=120)
    assert not error, error
    assert base64.b64decode(data["step_base64"]).startswith(b"ISO-10303-21")
    bb = data["bounding_box"]
    assert bb["max_x"] - bb["min_x"] == pytest.approx(130.0, abs=0.5)
    assert _runner_processes() == []


def test_huge_output_hits_the_file_size_limit() -> None:
    _, error = _script("while True:\n    print('x' * 100000)")
    assert "file size limit" in error, error
    assert _runner_processes() == []


def test_an_existing_output_path_is_never_overwritten() -> None:
    name = f"output/live-{uuid.uuid4().hex}.step"
    first, error = _script("result = cq.Workplane().box(1, 1, 1)", output_path=name)
    assert not error, error
    _, error = _script("result = cq.Workplane().box(2, 2, 2)", output_path=name)
    assert "never overwritten" in error, error


# -- outputs the adapter writes for the gateway --------------------------------
#
# The gateway and the MCP sidecar run as root and share /workspace with the
# adapter (uid 1000). A folder they create there is root's, mode 0755: the
# adapter can read what is in it but not write into it. An assembly the
# gateway asked to be written into such a folder used to log "Assembly
# created" over an empty folder. CADQUERY_ISOLATION_LIVE_AS_GATEWAY is a
# command prefix that runs a command as root on the same /workspace volume,
# as the gateway does (CI: docker run --user 0 --volumes-from the adapter).

AS_GATEWAY = shlex.split(os.environ.get("CADQUERY_ISOLATION_LIVE_AS_GATEWAY", ""))
needs_gateway = pytest.mark.skipif(
    not AS_GATEWAY, reason="needs CADQUERY_ISOLATION_LIVE_AS_GATEWAY (see above)"
)


def _tool(tool_id: str, arguments: dict[str, Any], timeout: float = 180) -> tuple[Any, str]:
    """Call any tool; return (data, error message)."""
    import httpx

    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "tool/call",
        "params": {"tool_id": tool_id, "arguments": arguments},
    }
    response = httpx.post(URL, json=body, timeout=timeout)
    payload = response.json()
    if "error" in payload:
        err = payload["error"]
        return {}, f"{err.get('message', '')} {json.dumps(err.get('data', ''))}"
    return payload["result"]["data"], ""


def _as_gateway(script: str) -> None:
    done = subprocess.run(  # noqa: S603
        [*AS_GATEWAY, "sh", "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr


@needs_gateway
def test_an_assembly_of_gateway_staged_parts_is_written_where_the_adapter_can() -> None:
    parts = []
    for size in (10, 20):
        data, error = _script(f"result = cq.Workplane().box({size}, {size}, 5)")
        assert not error, error
        parts.append(data["cad_file"])
    run = uuid.uuid4().hex[:12]
    staged = f"/workspace/_staged_work_products/{run}"
    gateway_folder = f"/workspace/_assemblies/{run}"
    _as_gateway(
        f"mkdir -p {staged} {gateway_folder} && "
        f"cp {parts[0]} {staged}/base.step && cp {parts[1]} {staged}/cover.step"
    )
    owner = _in_container("stat", "-c", "%u %a", gateway_folder).stdout.split()
    assert owner == ["0", "755"], owner
    placed = [
        {"name": "base", "file": f"{staged}/base.step"},
        {"name": "cover", "file": f"{staged}/cover.step", "location": {"z": 5}},
    ]

    # What the gateway does now: no output_path; the adapter picks the folder.
    data, error = _tool("cadquery.create_assembly", {"parts": placed})
    assert not error, error
    written = data["assembly_file"]
    assert written.startswith("/workspace/output/cadquery/assembly/"), written
    assert _in_container("test", "-s", written).returncode == 0
    assert data["file_size_bytes"] > 0

    # What it did before: a folder the adapter cannot write. Now an error.
    target = f"{gateway_folder}/desk-lamp-assembly.step"
    _, error = _tool("cadquery.create_assembly", {"parts": placed, "output_path": target})
    assert "not writable by this adapter" in error, error
    assert not _exists(target)


def test_a_board_sized_step_validates_as_one_compound_body() -> None:
    """A KiCad board export is a few MB of STEP: over aiohttp's 1 MiB default."""
    data, error = _script(
        # About 0.8 KB of base64 per box (CI measured 900 boxes at 0.7 MB).
        "result = cq.Workplane('XY').rarray(3, 3, 70, 70).box(2, 2, 1.6, combine=False)",
        timeout=240,
    )
    assert not error, error
    step_b64 = data["step_base64"]
    assert len(step_b64) > 1.5 * 1024 * 1024, len(step_b64)  # over the old 1 MiB

    out, error = _tool("cadquery.validate_shape", {"step_base64": step_b64, "board": True})
    assert not error, error
    assert out["outcome"] == "pass", out["findings"]
    assert out["body"] == "board"
    assert out["solid_count"] == 4900


def test_the_adapter_is_still_healthy() -> None:
    import httpx

    health = httpx.get(URL.rsplit("/", 1)[0] + "/health", timeout=10)
    assert health.status_code == 200
