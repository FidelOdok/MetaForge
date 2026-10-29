"""SimReady asset adapter configuration (FORGE-711)."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


def _workspace() -> str:
    return os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace")


class SimReadyConfig(BaseModel):
    """Where packages come from and where an import puts them."""

    workspace_dir: str = Field(
        default_factory=_workspace,
        description="The shared adapter workspace; imports go under simready/ in it, so "
        "twin.record_design_files can read them by the same path",
    )
    index_repo: str = Field(
        default="NVIDIA-Omniverse/simready-central",
        description="GitHub repo whose submissions/ folder lists every package URL",
    )
    index_ref: str = Field(default="main", description="Branch or commit of the index repo")
    github_api: str = Field(default="https://api.github.com")
    github_raw: str = Field(default="https://raw.githubusercontent.com")
    hf_api: str = Field(default="https://huggingface.co/api/datasets")
    index_ttl_s: float = Field(
        default=3600.0, ge=0, description="How long a fetched index or licence is reused"
    )
    timeout_s: float = Field(default=60.0, gt=0)
    max_package_mb: float = Field(
        default=200.0,
        gt=0,
        description="Refuse a package whose selected files exceed this; the layers of a "
        "typical asset are ~60 MB, its textures can be 1 GB more",
    )
