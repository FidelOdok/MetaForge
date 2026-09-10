"""Gazebo Sim solver wrapper -- subprocess invocation with timeout handling.

First slice (MET-633): runs the ``gz sim`` CLI headless against an SDF/world
file for a fixed number of iterations and captures stdout/stderr/exit code
plus an optional stats file the run produced. Deep physics telemetry
(per-link contact forces, trajectories) is deliberately out of scope here --
see MET-635 for the open question of a real GPU-accelerated dynamics
adapter with richer result extraction. This module gets you "did the
simulation run, and what did it report", the same altitude as CalculiX's
``run_fea`` before its .frd parsing.

CLI invocation note: ``gz sim -s -r --iterations <N> <world_file>`` matches
the Gazebo Sim (Harmonic/Fortress) CLI as documented, but hasn't been run
against a real ``gz`` binary in this environment -- verify the exact flags
against the installed Gazebo version before relying on this in production,
the way MET-380 caught a Debian packaging gotcha for CalculiX's ``ccx``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.gazebo.solver")

# Maximum simulation timeout in seconds
MAX_SIM_TIMEOUT = 300

# Gazebo Sim's default max_step_size is 1ms -- used to convert a requested
# wall/sim duration into an --iterations count for the headless CLI.
_DEFAULT_STEP_SIZE_S = 0.001

_VALID_WORLD_SUFFIXES = (".sdf", ".world")


class SolverError(Exception):
    """Raised when the Gazebo Sim solver fails."""

    def __init__(self, message: str, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class SolverTimeoutError(SolverError):
    """Raised when the solver exceeds the timeout."""


async def run_simulation(
    world_file: str,
    duration_s: float,
    timeout: int = MAX_SIM_TIMEOUT,
    gz_binary: str = "gz",
    work_dir: str | None = None,
    headless: bool = True,
) -> dict[str, Any]:
    """Run a Gazebo Sim world for a fixed simulated duration via subprocess.

    Args:
        world_file: Path to the .sdf/.world file describing the scene.
        duration_s: Simulated duration to run, in seconds (converted to an
            ``--iterations`` count using Gazebo's default 1ms step size).
        timeout: Maximum wall-clock time in seconds (capped at MAX_SIM_TIMEOUT).
        gz_binary: Path to the ``gz`` CLI binary.
        work_dir: Working directory for the run. Defaults to the world file's directory.
        headless: Run server-only (``-s``), no GUI/rendering.

    Returns:
        Dict with keys: result_files, stdout, stderr, returncode, sim_time_s,
        iterations, world_file, work_dir.

    Raises:
        SolverError: If the solver returns a non-zero exit code.
        SolverTimeoutError: If the solver exceeds the timeout.
        FileNotFoundError: If the world file does not exist.
        ValueError: If arguments are invalid.
    """
    effective_timeout = min(timeout, MAX_SIM_TIMEOUT)

    world_path = Path(world_file)
    if not world_path.exists():
        raise FileNotFoundError(f"World file not found: {world_file}")

    if world_path.suffix not in _VALID_WORLD_SUFFIXES:
        raise ValueError(
            f"World file must be one of {_VALID_WORLD_SUFFIXES}, got: {world_path.suffix}"
        )

    if duration_s <= 0:
        raise ValueError(f"duration_s must be positive, got: {duration_s}")

    effective_work_dir = work_dir or str(world_path.parent)
    iterations = max(1, int(duration_s / _DEFAULT_STEP_SIZE_S))

    if not shutil.which(gz_binary):
        raise SolverError(f"Gazebo Sim binary not found: {gz_binary}")

    args = [gz_binary, "sim"]
    if headless:
        args.append("-s")
    args += ["-r", "--iterations", str(iterations), str(world_path)]

    with tracer.start_as_current_span("gazebo.run_simulation") as span:
        span.set_attribute("gazebo.world_file", world_file)
        span.set_attribute("gazebo.duration_s", duration_s)
        span.set_attribute("gazebo.iterations", iterations)
        span.set_attribute("gazebo.timeout_s", effective_timeout)

        logger.info(
            "Starting Gazebo Sim",
            world_file=world_file,
            duration_s=duration_s,
            iterations=iterations,
            timeout=effective_timeout,
        )

        start_time = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=effective_work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )

        except TimeoutError:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass

            elapsed = time.monotonic() - start_time
            span.set_attribute("gazebo.timed_out", True)
            span.set_attribute("gazebo.elapsed_s", elapsed)

            logger.error(
                "Gazebo Sim timed out",
                world_file=world_file,
                timeout=effective_timeout,
                elapsed_s=elapsed,
            )
            raise SolverTimeoutError(
                f"Gazebo Sim timed out after {effective_timeout}s",
                returncode=None,
                stderr="",
            )

        except Exception as exc:
            span.record_exception(exc)
            raise

        elapsed = time.monotonic() - start_time
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        span.set_attribute("gazebo.elapsed_s", elapsed)
        span.set_attribute("gazebo.returncode", process.returncode or 0)

        if process.returncode != 0:
            logger.error(
                "Gazebo Sim failed",
                returncode=process.returncode,
                stderr=stderr_str[:500],
            )
            raise SolverError(
                f"Gazebo Sim exited with code {process.returncode}",
                returncode=process.returncode,
                stderr=stderr_str,
            )

        # Result files: a stats JSON is an opt-in convention (see
        # result_parser.py) -- a world's plugin config may dump one
        # named "<world_stem>.stats.json" into the work dir.
        work_path = Path(effective_work_dir)
        result_files: list[str] = []
        stats_path = work_path / f"{world_path.stem}.stats.json"
        if stats_path.exists():
            result_files.append(str(stats_path))

        logger.info(
            "Gazebo Sim completed",
            world_file=world_file,
            elapsed_s=round(elapsed, 2),
            iterations=iterations,
            result_files=result_files,
        )

        return {
            "result_files": result_files,
            "stdout": stdout_str,
            "stderr": stderr_str,
            "returncode": process.returncode,
            "sim_time_s": round(iterations * _DEFAULT_STEP_SIZE_S, 3),
            "wall_time_s": round(elapsed, 2),
            "iterations": iterations,
            "world_file": world_file,
            "work_dir": effective_work_dir,
        }
