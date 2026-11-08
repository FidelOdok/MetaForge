"""The isolated child that runs one ``cadquery.execute_script`` script.

The adapter (:mod:`tool_registry.tools.cadquery.script_runner`) starts this
file as a fresh Python process for every script, with ``python -I`` and a
clean environment. It reads one JSON spec on stdin and writes one JSON result
on the file descriptor that was its stdout. Nothing else crosses back.

Before any script code runs, and before CadQuery is imported (so before any
thread exists), the child locks itself down in this order:

1. creates its private work folder (mode 0700) and makes it the working
   directory; stdout and stderr go to ``console.log`` inside it;
2. resource limits: address space, CPU seconds, file size, open files, core
   dumps, and processes when it runs under its own uid;
3. Landlock: the file system is read-only everywhere it may read (the Python
   installation, ``/usr``, ``/etc``, the adapter's code), unreadable
   everywhere else (``/workspace`` included), and writable only inside the
   private folder; executing any file is refused; with Landlock ABI 4 or
   later every TCP bind and connect is refused too;
4. seccomp, on every thread: ``socket()`` for anything but a Unix socket,
   ``fork``/``vfork``/``clone`` of a new process, ``execve``, ``ptrace``,
   ``io_uring`` and the namespace, mount, keyring and BPF calls are refused.

A layer the spec lists as required that cannot be applied stops the child
before the script runs, with ``kind`` ``isolation_unavailable``.

This module must stay importable without CadQuery and must not import
anything heavy at module level: it runs before the lockdown.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import errno
import json
import os
import platform
import struct
import sys
import traceback
from typing import Any

# --------------------------------------------------------------------------
# Landlock (Linux 5.13+, unprivileged)
# --------------------------------------------------------------------------

_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13  # ABI 2
_FS_TRUNCATE = 1 << 14  # ABI 3
_FS_IOCTL_DEV = 1 << 15  # ABI 5
_NET_BIND_TCP = 1 << 0  # ABI 4
_NET_CONNECT_TCP = 1 << 1  # ABI 4

# --------------------------------------------------------------------------
# seccomp
# --------------------------------------------------------------------------

_AUDIT_ARCH_X86_64 = 0xC000003E
_AUDIT_ARCH_AARCH64 = 0xC00000B7
_SECCOMP_SET_MODE_FILTER = 1
_SECCOMP_FILTER_FLAG_TSYNC = 1
_RET_KILL_PROCESS = 0x80000000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JGE_K = 0x35
_BPF_JSET_K = 0x45
_BPF_RET_K = 0x06
_AF_UNIX = 1
_CLONE_THREAD = 0x00010000
_X32_SYSCALL_BIT = 0x40000000

# Syscall numbers per architecture. Only the ones the filter names.
_SYSCALLS: dict[str, dict[str, int]] = {
    "x86_64": {
        "seccomp": 317,
        "socket": 41,
        "clone": 56,
        "fork": 57,
        "vfork": 58,
        "execve": 59,
        "execveat": 322,
        "clone3": 435,
        "ptrace": 101,
        "process_vm_readv": 310,
        "process_vm_writev": 311,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "unshare": 272,
        "setns": 308,
        "mount": 165,
        "umount2": 166,
        "pivot_root": 155,
        "chroot": 161,
        "keyctl": 250,
        "add_key": 248,
        "request_key": 249,
        "bpf": 321,
        "perf_event_open": 298,
        "userfaultfd": 323,
    },
    "aarch64": {
        "seccomp": 277,
        "socket": 198,
        "clone": 220,
        "execve": 221,
        "execveat": 281,
        "clone3": 435,
        "ptrace": 117,
        "process_vm_readv": 270,
        "process_vm_writev": 271,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "unshare": 97,
        "setns": 268,
        "mount": 40,
        "umount2": 39,
        "pivot_root": 41,
        "chroot": 51,
        "keyctl": 219,
        "add_key": 217,
        "request_key": 218,
        "bpf": 280,
        "perf_event_open": 241,
        "userfaultfd": 282,
    },
}
_ARCH = {"x86_64": _AUDIT_ARCH_X86_64, "aarch64": _AUDIT_ARCH_AARCH64}

# Calls refused outright, and the errno they fail with.
_DENIED: tuple[tuple[str, int], ...] = (
    ("fork", errno.EPERM),
    ("vfork", errno.EPERM),
    ("execve", errno.EPERM),
    ("execveat", errno.EPERM),
    # glibc falls back to clone() on ENOSYS, which the filter can inspect.
    ("clone3", errno.ENOSYS),
    ("ptrace", errno.EPERM),
    ("process_vm_readv", errno.EPERM),
    ("process_vm_writev", errno.EPERM),
    ("io_uring_setup", errno.ENOSYS),
    ("io_uring_enter", errno.ENOSYS),
    ("io_uring_register", errno.ENOSYS),
    ("unshare", errno.EPERM),
    ("setns", errno.EPERM),
    ("mount", errno.EPERM),
    ("umount2", errno.EPERM),
    ("pivot_root", errno.EPERM),
    ("chroot", errno.EPERM),
    ("keyctl", errno.EPERM),
    ("add_key", errno.EPERM),
    ("request_key", errno.EPERM),
    ("bpf", errno.EPERM),
    ("perf_event_open", errno.EPERM),
    ("userfaultfd", errno.EPERM),
)


class IsolationUnavailableError(RuntimeError):
    """A required isolation layer could not be applied."""


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.restype = ctypes.c_int
    return libc


def _no_new_privs(libc: Any) -> None:
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise IsolationUnavailableError(
            f"prctl(PR_SET_NO_NEW_PRIVS) failed: {os.strerror(ctypes.get_errno())}"
        )


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, or 0 when Landlock is unavailable."""
    if not sys.platform.startswith("linux"):
        return 0
    libc = _libc()
    abi = libc.syscall(
        _SYS_LANDLOCK_CREATE_RULESET, None, ctypes.c_size_t(0), _LANDLOCK_CREATE_RULESET_VERSION
    )
    return int(abi) if abi > 0 else 0


def _fs_rights(abi: int) -> int:
    rights = (1 << 13) - 1
    if abi >= 2:
        rights |= _FS_REFER
    if abi >= 3:
        rights |= _FS_TRUNCATE
    if abi >= 5:
        rights |= _FS_IOCTL_DEV
    return rights


def apply_landlock(read_paths: list[str], write_dir: str, read_files: list[str]) -> int:
    """Confine the process: read ``read_paths``, write only ``write_dir``.

    Returns the ABI that was applied. Raises :class:`IsolationUnavailableError`
    when Landlock is not available or a call fails.
    """
    abi = landlock_abi()
    if abi < 1:
        raise IsolationUnavailableError("Landlock is not available in this kernel")
    libc = _libc()
    handled_fs = _fs_rights(abi)
    if abi >= 4:
        attr = struct.pack("=QQ", handled_fs, _NET_BIND_TCP | _NET_CONNECT_TCP)
    else:
        attr = struct.pack("=Q", handled_fs)
    buf = ctypes.create_string_buffer(attr, len(attr))
    ruleset = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, buf, ctypes.c_size_t(len(attr)), 0)
    if ruleset < 0:
        raise IsolationUnavailableError(
            f"landlock_create_ruleset failed: {os.strerror(ctypes.get_errno())}"
        )
    read_dir = _FS_READ_FILE | _FS_READ_DIR
    write = (
        _FS_WRITE_FILE
        | _FS_READ_FILE
        | _FS_READ_DIR
        | _FS_REMOVE_DIR
        | _FS_REMOVE_FILE
        | _FS_MAKE_DIR
        | _FS_MAKE_REG
        | _FS_MAKE_SYM
    )
    if abi >= 2:
        write |= _FS_REFER
    if abi >= 3:
        write |= _FS_TRUNCATE
    dev = _FS_READ_FILE | _FS_WRITE_FILE
    if abi >= 5:
        dev |= _FS_IOCTL_DEV

    def add(path: str, access: int, *, required: bool) -> None:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            if required:
                raise IsolationUnavailableError(f"cannot open {path!r} for Landlock") from None
            return
        try:
            rule = ctypes.create_string_buffer(struct.pack("=Qi", access & handled_fs, fd), 12)
            if libc.syscall(_SYS_LANDLOCK_ADD_RULE, ruleset, _LANDLOCK_RULE_PATH_BENEATH, rule, 0):
                err = ctypes.get_errno()
                if required:
                    raise IsolationUnavailableError(
                        f"landlock_add_rule({path!r}) failed: {os.strerror(err)}"
                    )
        finally:
            os.close(fd)

    try:
        for path in read_paths:
            add(path, read_dir, required=False)
        for path in read_files:
            add(path, _FS_READ_FILE, required=False)
        add("/dev", dev, required=False)
        add(write_dir, write, required=True)
        _no_new_privs(libc)
        if libc.syscall(_SYS_LANDLOCK_RESTRICT_SELF, ruleset, 0) != 0:
            raise IsolationUnavailableError(
                f"landlock_restrict_self failed: {os.strerror(ctypes.get_errno())}"
            )
    finally:
        os.close(ruleset)
    return abi


def _seccomp_program(machine: str) -> bytes:
    nr = _SYSCALLS[machine]
    insns: list[tuple[int, int, int, int]] = []

    def ins(code: int, jt: int, jf: int, k: int) -> None:
        insns.append((code, jt, jf, k))

    # Wrong architecture: kill. (Also catches 32-bit entry points.)
    ins(_BPF_LD_W_ABS, 0, 0, 4)
    ins(_BPF_JEQ_K, 1, 0, _ARCH[machine])
    ins(_BPF_RET_K, 0, 0, _RET_KILL_PROCESS)
    ins(_BPF_LD_W_ABS, 0, 0, 0)
    if machine == "x86_64":
        # The x32 ABI shares the arch value; refuse it outright.
        ins(_BPF_JGE_K, 0, 1, _X32_SYSCALL_BIT)
        ins(_BPF_RET_K, 0, 0, _RET_KILL_PROCESS)
    for name, err in _DENIED:
        if name not in nr:
            continue
        ins(_BPF_JEQ_K, 0, 1, nr[name])
        ins(_BPF_RET_K, 0, 0, _RET_ERRNO | err)
    # socket(domain, ...): only AF_UNIX.
    ins(_BPF_JEQ_K, 0, 4, nr["socket"])
    ins(_BPF_LD_W_ABS, 0, 0, 16)
    ins(_BPF_JEQ_K, 0, 1, _AF_UNIX)
    ins(_BPF_RET_K, 0, 0, _RET_ALLOW)
    ins(_BPF_RET_K, 0, 0, _RET_ERRNO | errno.EACCES)
    # clone(flags, ...): a thread (CLONE_THREAD) only, never a new process.
    ins(_BPF_JEQ_K, 0, 4, nr["clone"])
    ins(_BPF_LD_W_ABS, 0, 0, 16)
    ins(_BPF_JSET_K, 0, 1, _CLONE_THREAD)
    ins(_BPF_RET_K, 0, 0, _RET_ALLOW)
    ins(_BPF_RET_K, 0, 0, _RET_ERRNO | errno.EPERM)
    ins(_BPF_RET_K, 0, 0, _RET_ALLOW)
    return b"".join(struct.pack("=HBBI", *i) for i in insns)


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def apply_seccomp() -> None:
    """Install the syscall filter on every thread of this process."""
    if not sys.platform.startswith("linux"):
        raise IsolationUnavailableError("seccomp needs Linux")
    machine = platform.machine()
    if machine not in _SYSCALLS:
        raise IsolationUnavailableError(f"no seccomp filter for architecture {machine!r}")
    program = _seccomp_program(machine)
    buf = ctypes.create_string_buffer(program, len(program))
    prog = _SockFprog(len(program) // 8, ctypes.cast(buf, ctypes.c_void_p))
    libc = _libc()
    _no_new_privs(libc)
    rc = libc.syscall(
        _SYSCALLS[machine]["seccomp"],
        _SECCOMP_SET_MODE_FILTER,
        _SECCOMP_FILTER_FLAG_TSYNC,
        ctypes.byref(prog),
    )
    if rc != 0:
        raise IsolationUnavailableError(f"seccomp filter failed: {os.strerror(ctypes.get_errno())}")


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def _read_paths(repo_root: str) -> list[str]:
    """Directories the child may read: the system and the Python installation."""
    candidates = [
        "/usr",
        "/lib",
        "/lib64",
        "/lib32",
        "/bin",
        "/sbin",
        "/etc",
        "/sys/devices/system/cpu",
        "/sys/fs/cgroup",
        "/proc/self",
        sys.prefix,
        sys.base_prefix,
        sys.exec_prefix,
        os.path.dirname(os.path.realpath(sys.executable)),
        repo_root,
        *sys.path,
    ]
    out: list[str] = []
    for path in candidates:
        if not path:
            continue
        real = os.path.realpath(path)
        if real == "/" or not os.path.isdir(real) or real in out:
            continue
        out.append(real)
    return out


_READ_FILES = ["/proc/cpuinfo", "/proc/meminfo", "/proc/stat"]


def _set_limits(limits: dict[str, int], own_uid: bool) -> None:
    import resource  # POSIX only; imported here so the module loads anywhere

    def cap(which: int, value: int) -> None:
        resource.setrlimit(which, (value, value))

    cap(resource.RLIMIT_AS, int(limits["memory_bytes"]))
    cpu = int(limits["cpu_seconds"])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    cap(resource.RLIMIT_FSIZE, int(limits["file_bytes"]))
    cap(resource.RLIMIT_NOFILE, int(limits["open_files"]))
    cap(resource.RLIMIT_CORE, 0)
    if own_uid:
        # Counted per real uid, so only meaningful when the uid is this run's own.
        cap(resource.RLIMIT_NPROC, int(limits["processes"]))


def _classify(exc: BaseException) -> str:
    if isinstance(exc, MemoryError):
        return "memory_limit"
    if isinstance(exc, OSError) and exc.errno == errno.EFBIG:
        return "file_size_limit"
    return "script_error"


def _emit(fd: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def main() -> int:
    # ``python -I file.py`` puts this file's folder first on sys.path, which
    # would let its sibling modules (config, faces, ...) shadow real ones.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
    raw = sys.stdin.buffer.read(4 * 1024 * 1024)
    spec = json.loads(raw)
    result_fd = os.dup(1)
    os.set_inheritable(result_fd, False)
    workdir: str = spec["workdir"]
    required = set(spec.get("require", []))
    own_uid = bool(spec.get("own_uid"))
    isolation: dict[str, Any] = {"uid": os.getuid(), "own_uid": own_uid}

    def fail(kind: str, message: str) -> int:
        _emit(result_fd, {"ok": False, "kind": kind, "error": message, "isolation": isolation})
        return 0

    os.umask(0o077)
    try:
        os.mkdir(workdir, 0o700)
    except OSError as exc:
        return fail("isolation_unavailable", f"cannot create the private work folder: {exc}")
    os.chdir(workdir)
    console = os.open("console.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(console, 1)
    os.dup2(console, 2)
    os.close(console)
    os.close(devnull)
    with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as f:  # noqa: PTH123
        # Under memory pressure the kernel kills this child, not the adapter.
        f.write("1000")

    try:
        _set_limits(spec["limits"], own_uid)
    except (OSError, ValueError) as exc:
        return fail("isolation_unavailable", f"resource limits could not be set: {exc}")

    repo_root = spec["repo_root"]
    try:
        isolation["landlock_abi"] = apply_landlock(_read_paths(repo_root), workdir, _READ_FILES)
    except IsolationUnavailableError as exc:
        isolation["landlock_abi"] = 0
        if "landlock" in required:
            return fail("isolation_unavailable", f"Landlock: {exc}")
    try:
        apply_seccomp()
        isolation["seccomp"] = True
    except IsolationUnavailableError as exc:
        isolation["seccomp"] = False
        if "seccomp" in required:
            return fail("isolation_unavailable", f"seccomp: {exc}")

    # Locked down. From here on, nothing below can widen what the process may do.
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from tool_registry.tools.cadquery import operations as ops
    except Exception as exc:  # noqa: BLE001
        return fail("script_error", f"the CadQuery operations module failed to load: {exc}")

    suffix: str = spec["suffix"]
    out_name = "result" + suffix
    try:
        result_obj = ops._run_script_in_namespace(spec["script"])
    except BaseException as exc:  # noqa: BLE001
        return fail(_classify(exc), f"{type(exc).__name__}: {exc}")
    if result_obj is None:
        return fail("no_result", "Script must assign its output to a variable named 'result'")
    if not ops.HAS_CADQUERY:
        return fail("cadquery_unavailable", "CadQuery is not installed in the runner")
    try:
        ops._export_shape(result_obj, os.path.join(workdir, out_name))
        props = ops.shape_properties(result_obj)
        with open(out_name, "rb") as f:  # noqa: PTH123
            data = f.read(int(spec["limits"]["file_bytes"]) + 1)
    except BaseException as exc:  # noqa: BLE001
        kind = _classify(exc)
        return fail(kind if kind != "script_error" else "export_failed", f"{exc}")
    _emit(
        result_fd,
        {
            "ok": True,
            "output_b64": base64.b64encode(data).decode("ascii"),
            "properties": props,
            "isolation": isolation,
        },
    )
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        code = 3
    sys.stdout.flush()
    os._exit(code)
