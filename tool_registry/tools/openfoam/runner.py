"""Run OpenFOAM commands in their own environment (FORGE-557).

Every command is run through ``bash`` after sourcing OpenFOAM's ``bashrc``,
inside the case directory, with its output sent to ``log.<stage>``. A stage that
outruns its budget has its whole process group killed: ``mpirun`` leaves child
ranks behind otherwise, and a stray solver holding every core on a shared host is
worse than a failed run.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StageResult:
    name: str
    returncode: int
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def log_tail(case_dir: Path, stage: str, lines: int = 40) -> str:
    """The end of a stage's log, for an error a person has to act on."""
    path = case_dir / f"log.{stage}"
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


async def run_stage(
    bashrc: str, case_dir: Path, name: str, argv: list[str], timeout: float
) -> StageResult:
    """Run one OpenFOAM command; its output goes to ``log.<name>``."""
    script = (
        f"source {shlex.quote(bashrc)} && cd {shlex.quote(str(case_dir))} && "
        f"{shlex.join(argv)} > log.{name} 2>&1"
    )
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        script,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=max(timeout, 0.1))
        return StageResult(name, rc, round(time.monotonic() - started, 3))
    except TimeoutError:
        _kill_group(proc.pid)
        await proc.wait()
        return StageResult(name, -9, round(time.monotonic() - started, 3), timed_out=True)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
