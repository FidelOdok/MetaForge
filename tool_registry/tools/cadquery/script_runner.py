"""Run one ``cadquery.execute_script`` script in an OS-isolated child process.

The adapter never executes agent code in its own process. For each script it
starts :mod:`tool_registry.tools.cadquery.script_child` as a fresh Python
process (``python -I -B``, a clean environment, no inherited descriptors) and
waits for one JSON result on the child's stdout.

What isolates the child, in the order it is applied:

* **Its own uid** (when ``CADQUERY_RUNNER_UIDS`` names a pool): the child is
  started through ``setpriv`` as an unprivileged uid from the pool, with no
  supplementary groups and no capabilities, so file permissions keep it out
  of the adapter's files and it cannot signal the adapter. Each concurrent run
  has a different uid. The adapter needs ``CAP_SETUID`` and ``CAP_SETGID`` (as
  ambient capabilities) for this and nothing else.
* **Resource limits** (``setrlimit``, set by the child before the script
  runs): resident memory (``RLIMIT_DATA``, ``CADQUERY_RUNNER_MEMORY_MB``)
  under an address-space backstop (``RLIMIT_AS``,
  ``CADQUERY_RUNNER_ADDRESS_SPACE_MB``), CPU seconds, file size, open files,
  processes (under its own uid) and no core dumps; plus a wall-clock deadline
  the adapter enforces by killing every process of the run. The child's
  environment caps glibc malloc arenas and BLAS/OpenMP threads, so the
  address space it reserves stays close to what it uses (FORGE-832).
* **Landlock**: read-only system paths, nothing else readable (so not
  ``/workspace``), writable only in its private folder, no ``execve``, and on
  ABI 4+ no TCP bind or connect.
* **seccomp**: no non-Unix sockets, no new processes, no ``execve``, no
  ``ptrace``, no ``io_uring``, no namespace, mount, keyring or BPF calls.

The private folder is ``<root>/run-<random>`` on the runner's tmpfs
(``CADQUERY_RUNNER_ROOT``), created by the child itself (mode 0700) and
removed after the run. The result comes back only as bytes on the pipe: the
adapter never opens a path the child names. It checks the size, decodes the
base64 and, for STEP, the ISO-10303-21 header, then writes the bytes to the
output path the adapter itself chose.

``CADQUERY_RUNNER_REQUIRE`` lists the layers that must hold
(``uid,landlock,seccomp`` in the compose file). A required layer that cannot
be applied refuses the run, it never degrades silently.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cadquery.script_runner")

_CHILD = Path(__file__).with_name("script_child.py")
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Output formats the child may export, by suffix.
ALLOWED_SUFFIXES = frozenset({".step", ".stp", ".stl", ".brep", ".amf", ".svg", ".obj"})

LAYERS = ("uid", "landlock", "seccomp")

#: The child exits 0 or 3 itself; 127 is glibc failing to allocate a new
#: thread's TLS, which it reports by ``_exit(127)``.
_EXIT_NO_THREAD_MEMORY = 127

_metrics: Any = None


def _collector() -> Any:
    """Lazily resolved metrics collector (a no-op one without an OTel SDK)."""
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-cadquery-adapter")
    return _metrics


class ScriptIsolationError(RuntimeError):
    """The isolated runner cannot run a script here (a required layer is missing)."""


class ScriptLimitError(RuntimeError):
    """The script hit a resource limit and was stopped."""

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit


class ScriptRunTimeoutError(RuntimeError):
    """The script ran past its wall-clock deadline and was killed."""


class ScriptRunError(RuntimeError):
    """The script itself failed (an exception in the script, or a bad result)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RunnerLimits:
    """Per-run limits. Sizes in bytes, times in seconds."""

    #: What a script may really use: private writable memory (``RLIMIT_DATA``).
    memory_bytes: int = 2048 * 1024 * 1024
    #: Address-space backstop (``RLIMIT_AS``), far above any real script.
    #: OCCT, Python and glibc reserve address space they never touch, so this
    #: must not be the budget (FORGE-832).
    address_space_bytes: int = 16 * 1024 * 1024 * 1024
    file_bytes: int = 64 * 1024 * 1024
    open_files: int = 256
    processes: int = 256
    output_bytes: int = 64 * 1024 * 1024
    #: Added to the script timeout for interpreter and CadQuery start-up.
    startup_grace_s: float = 15.0


@dataclass
class RunResult:
    """What a successful run returns."""

    data: bytes
    properties: dict[str, Any]
    isolation: dict[str, Any]
    duration_s: float


def _parse_uid_pool(raw: str) -> list[int]:
    """``"61000-61003"`` or ``"61000,61005"`` to a list of uids (never 0)."""
    uids: list[int] = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            uids.extend(range(lo, hi + 1))
        else:
            uids.append(int(part))
    if any(u <= 0 for u in uids):
        raise ValueError("CADQUERY_RUNNER_UIDS must not include uid 0")
    return sorted(set(uids))


def _cap_eff() -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as f:  # noqa: PTH123
            for line in f:
                if line.startswith("CapEff:"):
                    return int(line.split()[1], 16)
    except OSError:
        pass
    return 0


_CAP_SETGID = 6
_CAP_SETUID = 7


def uid_switch_available() -> tuple[bool, str]:
    """Whether this process can start a child as another uid, and if not, why."""
    if not sys.platform.startswith("linux"):
        return False, "needs Linux"
    if shutil.which("setpriv") is None:
        return False, "setpriv (util-linux) is not installed"
    eff = _cap_eff()
    if os.geteuid() != 0 and not (eff >> _CAP_SETUID & 1 and eff >> _CAP_SETGID & 1):
        return False, "the adapter has no CAP_SETUID/CAP_SETGID"
    return True, ""


@dataclass
class ScriptRunner:
    """Starts and supervises isolated script children.

    One instance per adapter process: it owns the uid pool and the
    concurrency limit.
    """

    root: str
    uid_pool: list[int] = field(default_factory=list)
    require: frozenset[str] = frozenset({"landlock", "seccomp"})
    limits: RunnerLimits = field(default_factory=RunnerLimits)
    max_concurrent: int = 2
    python: str = sys.executable

    def __post_init__(self) -> None:
        unknown = set(self.require) - set(LAYERS)
        if unknown:
            raise ValueError(f"unknown isolation layer(s): {sorted(unknown)}")
        # With a uid pool, each concurrent run needs a uid of its own.
        slots = self.max_concurrent
        if self.uid_pool:
            slots = min(slots, len(self.uid_pool))
        self._slots = threading.BoundedSemaphore(max(1, slots))
        self._lock = threading.Lock()
        self._free_uids = list(self.uid_pool)

    @classmethod
    def from_env(cls) -> ScriptRunner:
        """Build the runner from ``CADQUERY_RUNNER_*`` variables."""
        env = os.environ
        root = env.get("CADQUERY_RUNNER_ROOT", "").strip()
        if not root:
            root = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cadquery-runner")  # noqa: S108
        pool = _parse_uid_pool(env.get("CADQUERY_RUNNER_UIDS", ""))
        raw_require = env.get("CADQUERY_RUNNER_REQUIRE")
        if raw_require is None:
            require = {"landlock", "seccomp"} | ({"uid"} if pool else set())
        else:
            require = {r.strip() for r in raw_require.split(",") if r.strip()}
        defaults = RunnerLimits()

        def num(name: str, default: float) -> float:
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("cadquery_runner_setting_invalid", variable=name, value=raw[:16])
                return default

        mib = 1024 * 1024
        limits = RunnerLimits(
            memory_bytes=int(num("CADQUERY_RUNNER_MEMORY_MB", defaults.memory_bytes / mib) * mib),
            address_space_bytes=int(
                num("CADQUERY_RUNNER_ADDRESS_SPACE_MB", defaults.address_space_bytes / mib) * mib
            ),
            file_bytes=int(num("CADQUERY_RUNNER_MAX_FILE_MB", defaults.file_bytes / mib) * mib),
            output_bytes=int(
                num("CADQUERY_RUNNER_MAX_OUTPUT_MB", defaults.output_bytes / mib) * mib
            ),
            startup_grace_s=num("CADQUERY_RUNNER_STARTUP_GRACE_S", defaults.startup_grace_s),
        )
        return cls(
            root=root,
            uid_pool=pool,
            require=frozenset(require),
            limits=limits,
            max_concurrent=int(num("CADQUERY_RUNNER_MAX_CONCURRENT", 2)),
        )

    # -- public ---------------------------------------------------------

    def run(self, script: str, suffix: str, timeout: float) -> RunResult:
        """Run ``script`` in a fresh isolated child; return the exported bytes.

        Raises :class:`ScriptIsolationError`, :class:`ScriptRunTimeoutError`,
        :class:`ScriptLimitError` or :class:`ScriptRunError`.
        """
        suffix = suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"unsupported output format {suffix!r}")
        with tracer.start_as_current_span("cadquery.script_runner.run") as span:
            span.set_attribute("runner.timeout_s", float(timeout))
            start = time.monotonic()
            outcome = "error"
            memory_limit: str | None = None
            try:
                result = self._run(script, suffix, timeout, span)
                outcome = "ok"
                # Reported by the child, so only a known value is logged.
                reported = result.isolation.get("memory_limit")
                memory_limit = reported if reported in ("data", "address_space") else None
                return result
            except ScriptRunTimeoutError:
                outcome = "timeout"
                _collector().record_cadquery_script_limit("timeout")
                raise
            except ScriptLimitError as exc:
                outcome = "limit"
                _collector().record_cadquery_script_limit(exc.limit)
                raise
            except ScriptIsolationError:
                outcome = "isolation_unavailable"
                raise
            except ScriptRunError:
                outcome = "script_error"
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise
            finally:
                duration = time.monotonic() - start
                span.set_attribute("runner.outcome", outcome)
                _collector().record_cadquery_script_run(outcome, duration)
                logger.info(
                    "cadquery_script_run",
                    outcome=outcome,
                    duration_s=round(duration, 3),
                    timeout_s=timeout,
                    memory_limit=memory_limit,
                )

    # -- internals ------------------------------------------------------

    def _check_platform(self) -> bool:
        if not sys.platform.startswith("linux"):
            raise ScriptIsolationError(
                "cadquery.execute_script runs scripts only in the isolated Linux runner; "
                "this adapter is not on Linux"
            )
        if "uid" in self.require and not self.uid_pool:
            raise ScriptIsolationError(
                "the runner must switch uid (CADQUERY_RUNNER_REQUIRE has 'uid') but "
                "CADQUERY_RUNNER_UIDS names no uid pool"
            )
        if self.uid_pool:
            ok, why = uid_switch_available()
            if not ok:
                if "uid" in self.require:
                    raise ScriptIsolationError(f"the runner cannot switch uid: {why}")
                logger.warning("cadquery_runner_uid_switch_unavailable", reason=why)
                return False
            return True
        return False

    def _prepare_root(self, use_uid: bool) -> None:
        os.makedirs(self.root, exist_ok=True)
        # Children of other uids may create their folder here but not list it.
        try:
            os.chmod(self.root, 0o1733 if use_uid else 0o700)
        except PermissionError:
            logger.warning("cadquery_runner_root_mode_unchanged", root=self.root)

    def _acquire_uid(self) -> int:
        with self._lock:
            return self._free_uids.pop()

    def _release_uid(self, uid: int) -> None:
        with self._lock:
            self._free_uids.append(uid)

    def _command(self, uid: int | None) -> list[str]:
        python = [self.python, "-I", "-B", str(_CHILD)]
        if uid is None:
            return python
        return [
            "setpriv",
            f"--reuid={uid}",
            f"--regid={uid}",
            "--clear-groups",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "--no-new-privs",
            "--",
            *python,
        ]

    def _env(self, workdir: str) -> dict[str, str]:
        # Nothing of the adapter's own environment (endpoints, keys) reaches the child.
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": workdir,
            "TMPDIR": workdir,
            "XDG_CACHE_HOME": workdir,
            "LANG": "C.UTF-8",
            # glibc gives each allocating thread its own arena, 64 MB of
            # reserved address space apiece, and OCCT runs one thread per CPU.
            "MALLOC_ARENA_MAX": "2",
            # One BLAS/OpenMP thread: the container has 2 CPUs for all runs.
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
        }

    def _run(self, script: str, suffix: str, timeout: float, span: Any) -> RunResult:
        use_uid = self._check_platform()
        if not self._slots.acquire(timeout=max(1.0, timeout)):
            raise ScriptIsolationError("every script runner slot is busy; try again shortly")
        uid: int | None = None
        try:
            uid = self._acquire_uid() if use_uid else None
            self._prepare_root(use_uid)
            workdir = os.path.join(self.root, f"run-{secrets.token_hex(16)}")
            # A backstop for CPU burnt on many threads; the wall clock decides a plain timeout.
            cpu = int(2 * (timeout + self.limits.startup_grace_s)) + 5
            spec = {
                "script": script,
                "suffix": suffix,
                "workdir": workdir,
                "repo_root": str(_REPO_ROOT),
                "own_uid": uid is not None,
                "require": sorted(self.require - {"uid"}),
                "limits": {
                    "memory_bytes": self.limits.memory_bytes,
                    "address_space_bytes": self.limits.address_space_bytes,
                    "cpu_seconds": cpu,
                    "file_bytes": self.limits.file_bytes,
                    "open_files": self.limits.open_files,
                    "processes": self.limits.processes,
                },
            }
            span.set_attribute("runner.uid_switch", uid is not None)
            logger.info(
                "cadquery_script_run_started",
                uid=uid,
                require=sorted(self.require),
                timeout_s=timeout,
            )
            start = time.monotonic()
            try:
                proc = subprocess.Popen(  # noqa: S603
                    self._command(uid),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.root,
                    env=self._env(workdir),
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError as exc:
                raise ScriptIsolationError(f"the script runner could not start: {exc}") from exc
            try:
                deadline = start + timeout + self.limits.startup_grace_s
                out, err, overflow, timed_out = self._exchange(
                    proc, json.dumps(spec).encode("utf-8"), deadline
                )
            finally:
                self._kill_and_clean(proc, uid, workdir)
            duration = time.monotonic() - start
            return self._interpret(proc.returncode, out, err, overflow, timed_out, suffix, duration)
        finally:
            if uid is not None:
                self._release_uid(uid)
            self._slots.release()

    def _exchange(
        self, proc: subprocess.Popen[bytes], spec: bytes, deadline: float
    ) -> tuple[bytes, bytes, bool, bool]:
        """Send the spec, read stdout and stderr with caps, until EOF or deadline."""
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        sel = selectors.DefaultSelector()
        pending = memoryview(spec)
        os.set_blocking(proc.stdin.fileno(), False)
        sel.register(proc.stdin, selectors.EVENT_WRITE)
        sel.register(proc.stdout, selectors.EVENT_READ)
        sel.register(proc.stderr, selectors.EVENT_READ)
        chunks: dict[int, list[bytes]] = {proc.stdout.fileno(): [], proc.stderr.fileno(): []}
        sizes = {proc.stdout.fileno(): 0, proc.stderr.fileno(): 0}
        caps = {
            # base64 of the output plus a little JSON.
            proc.stdout.fileno(): self.limits.output_bytes * 4 // 3 + 1024 * 1024,
            proc.stderr.fileno(): 64 * 1024,
        }
        overflow = False
        timed_out = False
        try:
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in sel.select(timeout=min(remaining, 1.0)):
                    fileobj: Any = key.fileobj
                    if fileobj is proc.stdin:
                        try:
                            n = os.write(fileobj.fileno(), pending[:65536])
                        except (BrokenPipeError, BlockingIOError) as exc:
                            if isinstance(exc, BlockingIOError):
                                continue
                            n = len(pending)
                        pending = pending[n:]
                        if not pending:
                            sel.unregister(fileobj)
                            fileobj.close()
                        continue
                    fd = fileobj.fileno()
                    data = os.read(fd, 65536)
                    if not data:
                        sel.unregister(fileobj)
                        continue
                    if sizes[fd] + len(data) > caps[fd]:
                        if fd == proc.stdout.fileno():
                            overflow = True
                            raise _Overflow
                        continue  # stderr past its cap is dropped, not fatal
                    sizes[fd] += len(data)
                    chunks[fd].append(data)
            if not timed_out:
                try:
                    proc.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
        except _Overflow:
            pass
        finally:
            sel.close()
        return (
            b"".join(chunks[proc.stdout.fileno()]),
            b"".join(chunks[proc.stderr.fileno()]),
            overflow,
            timed_out,
        )

    def _kill_and_clean(self, proc: subprocess.Popen[bytes], uid: int | None, workdir: str) -> None:
        """Kill every process of the run and remove its private folder."""
        if uid is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            # The adapter has no CAP_KILL and cannot remove the child's 0700
            # folder; a helper running as the run's uid does both. kill(-1)
            # reaches every process of that uid in this PID namespace.
            helper = [
                "setpriv",
                f"--reuid={uid}",
                f"--regid={uid}",
                "--clear-groups",
                "--inh-caps=-all",
                "--ambient-caps=-all",
                "--no-new-privs",
                "--",
                "/bin/sh",
                "-c",
                'kill -KILL -1 2>/dev/null; rm -rf -- "$1"',
                "cadquery-runner-clean",
                workdir,
            ]
            try:
                subprocess.run(  # noqa: S603
                    helper, env={"PATH": "/usr/bin:/bin"}, timeout=30, check=False, cwd="/"
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.error("cadquery_runner_cleanup_failed", uid=uid, error=str(exc))
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.error("cadquery_runner_child_not_reaped", uid=uid, pid=proc.pid)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _interpret(
        self,
        returncode: int | None,
        out: bytes,
        err: bytes,
        overflow: bool,
        timed_out: bool,
        suffix: str,
        duration: float,
    ) -> RunResult:
        if overflow:
            raise ScriptLimitError(
                "output",
                f"Script output exceeded the {self.limits.output_bytes // (1024 * 1024)} MB "
                "limit and the script was stopped",
            )
        payload: dict[str, Any] | None = None
        if out:
            try:
                parsed = json.loads(out)
                payload = parsed if isinstance(parsed, dict) else None
            except (ValueError, UnicodeDecodeError):
                payload = None
        if timed_out:
            raise ScriptRunTimeoutError(
                "Script execution exceeded its timeout and every process of the run was killed"
            )
        if payload is None:
            raise self._from_signal(returncode, err)
        if not payload.get("ok"):
            kind = str(payload.get("kind") or "script_error")
            message = str(payload.get("error") or "the script failed")[:4000]
            if kind == "isolation_unavailable":
                raise ScriptIsolationError(f"The isolated script runner refused to run: {message}")
            if kind == "memory_limit":
                raise ScriptLimitError(
                    "memory",
                    f"Script exceeded the {self.limits.memory_bytes // (1024 * 1024)} MB "
                    f"memory limit: {message}",
                )
            if kind == "file_size_limit":
                raise ScriptLimitError(
                    "file_size",
                    f"Script exceeded the {self.limits.file_bytes // (1024 * 1024)} MB "
                    f"file size limit: {message}",
                )
            raise ScriptRunError(kind, message)
        return self._decode_ok(payload, suffix, duration)

    def _from_signal(self, returncode: int | None, err: bytes) -> Exception:
        tail = err[-2000:].decode("utf-8", "replace").strip()
        if returncode is not None and returncode < 0:
            sig = -returncode
            if sig == signal.SIGXCPU or sig == signal.SIGKILL:
                return ScriptLimitError(
                    "cpu",
                    "Script exceeded its CPU time limit (or was killed for memory) and was "
                    f"stopped (signal {signal.Signals(sig).name})",
                )
            if sig == signal.SIGXFSZ:
                return ScriptLimitError("file_size", "Script exceeded the file size limit")
            try:
                name: object = signal.Signals(sig).name
            except ValueError:
                name = sig
            return ScriptRunError("crashed", f"The script runner crashed (signal {name}). {tail}")
        if returncode == _EXIT_NO_THREAD_MEMORY:
            # glibc's "cannot allocate memory for thread-local data: ABORT":
            # a thread could not start inside the memory limit.
            return ScriptLimitError(
                "memory",
                f"Script exceeded the {self.limits.memory_bytes // (1024 * 1024)} MB "
                "memory limit while starting a thread and was stopped",
            )
        return ScriptRunError(
            "no_result", f"The script runner returned no result (exit {returncode}). {tail}"
        )

    def _decode_ok(self, payload: dict[str, Any], suffix: str, duration: float) -> RunResult:
        raw = payload.get("output_b64")
        if not isinstance(raw, str):
            raise ScriptRunError("bad_result", "The script runner returned no output file")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ScriptRunError("bad_result", "The script runner returned bad output") from exc
        if len(data) > self.limits.output_bytes:
            raise ScriptLimitError("output", "Script output exceeded the output size limit")
        if suffix in (".step", ".stp") and not data.lstrip()[:12].startswith(b"ISO-10303-21"):
            raise ScriptRunError("bad_result", "The script runner's output is not a STEP file")
        props = _clean_properties(payload.get("properties"))
        isolation = payload.get("isolation")
        return RunResult(
            data=data,
            properties=props,
            isolation=isolation if isinstance(isolation, dict) else {},
            duration_s=duration,
        )


class _Overflow(Exception):
    pass


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ScriptRunError("bad_result", "The script runner returned bad properties")
    return float(value)


def _clean_properties(raw: Any) -> dict[str, Any]:
    """Accept only the numeric shape of ``_get_shape_properties`` from the child."""
    if not isinstance(raw, dict):
        raise ScriptRunError("bad_result", "The script runner returned no properties")
    bbox = raw.get("bounding_box")
    keys = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    clean_bbox = None
    if isinstance(bbox, dict):
        clean_bbox = {k: _num(bbox.get(k)) for k in keys}
    return {
        "volume_mm3": _num(raw.get("volume_mm3")),
        "surface_area_mm2": _num(raw.get("surface_area_mm2")),
        "bounding_box": clean_bbox,
    }
