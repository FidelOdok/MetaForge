"""Run Blender as a separate program on a job file (FORGE-628).

This module is MetaForge code and never imports ``bpy``. It writes a job
JSON, starts ``blender --background --factory-startup`` with the GPL script
in ``bpy_scripts/``, waits with a timeout, and reads the result JSON and the
files the script wrote. Keeping Blender at arm's length like this, exchanging
only data files, is what keeps the GPL on the script and on Blender, not on
the rest of MetaForge.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.blender.runner")

SCRIPTS = Path(__file__).parent / "bpy_scripts"


class BlenderError(RuntimeError):
    """Blender could not be run, failed, or wrote nothing usable."""


async def run_job(
    blender_binary: str, job: dict[str, Any], out_dir: Path, timeout_seconds: int
) -> dict[str, Any]:
    """Run ``render_scene.py`` on ``job``; return its result JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {**job, "out_dir": str(out_dir)}
    job_file = out_dir / "job.json"
    job_file.write_text(json.dumps(job), encoding="utf-8")
    command = [
        blender_binary,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(SCRIPTS / "render_scene.py"),
        "--",
        str(job_file),
    ]
    with tracer.start_as_current_span("blender.run_job") as span:
        span.set_attribute("blender.views", len(job.get("views") or []))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise BlenderError(f"Blender is not installed here ({blender_binary})") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise BlenderError(f"Blender did not finish within {timeout_seconds} s") from exc
        span.set_attribute("blender.exit_code", process.returncode or 0)
    result_file = out_dir / "result.json"
    if process.returncode != 0 or not result_file.exists():
        tail = (stderr or stdout or b"").decode(errors="replace").strip().splitlines()[-8:]
        logger.warning("blender_job_failed", exit_code=process.returncode, tail=tail)
        raise BlenderError(
            f"Blender exited with code {process.returncode}: " + " | ".join(tail)
            if tail
            else f"Blender exited with code {process.returncode} and wrote no result"
        )
    result: dict[str, Any] = json.loads(result_file.read_text(encoding="utf-8"))
    return result
