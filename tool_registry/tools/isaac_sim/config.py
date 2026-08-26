"""Isaac Sim adapter configuration (MET-635 physics + MET-636 rendering).

One container image (`nvcr.io/nvidia/isaac-sim`, verified 2026-08-27 via
NGC's catalog page) bundles both NVIDIA PhysX (physics) and RTX
(rendering) -- the same relationship as CalculiX's single `ccx` binary
serving both `run_fea` and `run_thermal`. One adapter, two tools, matches
that reality rather than an artificial physx/rtx split into two adapters.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IsaacSimConfig(BaseModel):
    """Configuration for the Isaac Sim tool adapter."""

    image: str = Field(
        default="nvcr.io/nvidia/isaac-sim",
        description="Container image (verified real image, NGC catalog 2026-08-27)",
    )
    tag: str = Field(default="6.0.1", description="Image tag")
    compute_provider: str | None = Field(
        default=None,
        description="Compute provider id for tool_registry.compute_providers.resolve_runtime() "
        "(None -> METAFORGE_COMPUTE_PROVIDER env, default 'docker'). Real GPU dispatch needs "
        "'runpod'/'vast_ai' -- but see RemoteVolumesUnsupportedError caveat in the module "
        "docstrings.",
    )
    work_dir: str = Field(default="/tmp/isaac_sim", description="Local working directory")
    timeout_seconds: int = Field(default=1800, ge=1, description="Max seconds for a run")
    accept_eula: bool = Field(
        default=False,
        description="Must be explicitly set true by the caller/deployment -- mirrors Isaac "
        "Sim's documented required ACCEPT_EULA=Y container env var. Never default this on.",
    )
