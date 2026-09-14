"""Isaac Sim container dispatch (MET-635 physics, MET-636 rendering).

Real, verified facts (2026-08-27, NGC catalog + Isaac Sim install docs):
- Image: ``nvcr.io/nvidia/isaac-sim:6.0.1`` -- bundles both PhysX and RTX.
- Required container env: ``ACCEPT_EULA=Y`` (documented). ``PRIVACY_CONSENT=Y``
  is an optional telemetry opt-in, not required -- not set here.
- GPU: RTX 4080 or higher, current NVIDIA driver, nvidia-container-toolkit.

NOT verified (flagged, not guessed as fact): the exact headless Python
script invocation syntax for running a physics step or rendering a
specific USD file. Isaac Sim's docs describe headless Python-app support
but the fetch used to write this module didn't quote a script invocation
example. So ``command`` is caller-supplied here, not something this
module invents -- authoring the actual Isaac Sim Python scripts that do
useful physics/rendering work is separate, not-yet-done work (matches
MET-635's "no reference implementation found yet" note in Linear).

Architectural constraint that shapes this module (2026-08-27): MET-564's
remote compute providers (``RunPodRuntime``/``VastAIRuntime``) do not
support ``ContainerConfig.volumes`` at all -- see
``tool_registry/compute_providers.py``. A USD scene can only actually be
mounted into the container when ``compute_provider`` resolves to local
``DockerRuntime``; a remote GPU provider raises
``RemoteVolumesUnsupportedError`` as soon as a ``usd_path`` is given, by
design, until MET-489's blob-store I/O lands. This module does not work
around that -- it surfaces the same error the compute-provider layer
already raises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tool_registry.compute_providers import resolve_runtime
from tool_registry.container_runtime import ContainerConfig

DEFAULT_IMAGE = "nvcr.io/nvidia/isaac-sim"
DEFAULT_TAG = "6.0.1"


class IsaacSimDispatchError(Exception):
    """Raised for Isaac Sim dispatch failures that aren't a missing file."""


async def _dispatch(
    command: list[str],
    usd_path: str | None,
    compute_provider: str | None,
    image: str,
    tag: str,
    timeout_seconds: int,
    accept_eula: bool,
) -> dict[str, Any]:
    if not command:
        raise ValueError("command is required")

    if not accept_eula:
        raise IsaacSimDispatchError(
            "accept_eula must be explicitly set true -- Isaac Sim's container requires "
            "ACCEPT_EULA=Y; MetaForge will not accept a EULA on your behalf silently."
        )

    volumes: dict[str, str] = {}
    if usd_path is not None:
        usd_file = Path(usd_path)
        if not usd_file.exists():
            raise FileNotFoundError(f"USD file not found: {usd_path}")
        volumes[str(usd_file.parent)] = "/workspace/input"

    config = ContainerConfig(
        image=image,
        tag=tag,
        env={"ACCEPT_EULA": "Y"},
        volumes=volumes,
        timeout_seconds=timeout_seconds,
    )

    runtime = resolve_runtime(compute_provider)
    result = await runtime.run(config, command)

    return {
        "success": result.success,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_seconds": result.duration_seconds,
    }


async def run_physics(
    command: list[str],
    usd_path: str | None = None,
    compute_provider: str | None = None,
    image: str = DEFAULT_IMAGE,
    tag: str = DEFAULT_TAG,
    timeout_seconds: int = 1800,
    accept_eula: bool = False,
) -> dict[str, Any]:
    """Dispatch a physics (PhysX) job to the Isaac Sim container.

    ``command`` is caller-supplied -- this module does not invent the
    Isaac Sim Python script invocation syntax (unverified, see module
    docstring).

    Raises:
        ValueError: If command is empty.
        IsaacSimDispatchError: If accept_eula is not explicitly true.
        FileNotFoundError: If usd_path is given but doesn't exist.
    """
    return await _dispatch(
        command, usd_path, compute_provider, image, tag, timeout_seconds, accept_eula
    )


async def render_scene(
    command: list[str],
    usd_path: str | None = None,
    compute_provider: str | None = None,
    image: str = DEFAULT_IMAGE,
    tag: str = DEFAULT_TAG,
    timeout_seconds: int = 1800,
    accept_eula: bool = False,
) -> dict[str, Any]:
    """Dispatch an RTX render job to the Isaac Sim container.

    ``command`` is caller-supplied -- see ``run_physics()`` docstring.

    Raises:
        ValueError: If command is empty.
        IsaacSimDispatchError: If accept_eula is not explicitly true.
        FileNotFoundError: If usd_path is given but doesn't exist.
    """
    return await _dispatch(
        command, usd_path, compute_provider, image, tag, timeout_seconds, accept_eula
    )
