"""What code is this process actually running (FORGE-411)?

The `mcp-http` sidecar on fidel-dev served two-day-old code while the checkout
beside it sat on current `main`. Nothing said so. The symptoms were a shorter
protocol version and missing tools, which look exactly like a smaller
deployment — so four bug reports were filed against that run, and two of the
findings in them were not real.

That is the expensive part of this bug, and it is not the stale image. It is
that staleness was **invisible**: establishing it took reading container
uptime, comparing a protocol version against the source, and inferring. The
fix is to make the question answerable in one call.

Two SHAs, because the sidecar is the case where they differ:

``build_sha``
    baked into the image at build time. What the *installed package* is.

``source_sha``
    the checkout mounted into the container right now. The gateway runs
    uvicorn's reloader so this is what it executes; the sidecar calls uvicorn
    programmatically, has no reloader, and therefore keeps running
    ``build_sha`` no matter what the mount says.

When they disagree, the process is running something other than the code
sitting next to it. For the sidecar that is the definition of the bug.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["BUILD_SHA_ENV", "CodeVersion", "code_version"]

#: Set at image build time (``--build-arg METAFORGE_BUILD_SHA=$(git rev-parse HEAD)``).
BUILD_SHA_ENV = "METAFORGE_BUILD_SHA"

#: Where the source is mounted in the dev containers.
_SOURCE_ROOT_ENV = "METAFORGE_SOURCE_ROOT"
_DEFAULT_SOURCE_ROOT = "/app"

_SHORT = 12


@dataclass(frozen=True)
class CodeVersion:
    """What this process is running, and whether that matches the checkout."""

    build_sha: str | None
    source_sha: str | None
    reloads: bool

    @property
    def stale(self) -> bool:
        """True only when we can *prove* the process is behind the checkout.

        Unknown is not stale. A deployment that bakes no build SHA, or runs
        with no mounted source, would otherwise report a problem on every
        health check -- and an alert that is always firing is an alert nobody
        reads, which is the failure this is trying to fix rather than repeat.
        """
        if self.reloads:
            # The reloader picks the mount up, so a difference is expected and
            # transient rather than a stale process.
            return False
        if not self.build_sha or not self.source_sha:
            return False
        return self.build_sha != self.source_sha

    @property
    def result(self) -> str:
        """``stale``, ``current`` or ``unknown`` -- three-valued on purpose.

        ``unknown`` is not ``current``. An image with no build SHA baked in
        cannot answer the question at all, and answering "current" anyway is
        the silent pass this whole module exists to end.
        """
        if self.stale:
            return "stale"
        if not self.build_sha or not self.source_sha:
            return "unknown" if not self.reloads else "current"
        return "current"

    def report(self) -> dict[str, object]:
        out: dict[str, object] = {
            "build_sha": (self.build_sha or "unknown")[:_SHORT],
            "source_sha": (self.source_sha or "unknown")[:_SHORT],
            "stale": self.stale,
            "result": self.result,
            # Said explicitly rather than left to be inferred from a
            # difference between the SHAs: for the gateway a difference is
            # normal, for the sidecar it is the bug.
            "reloads": self.reloads,
        }
        if self.stale:
            out["detail"] = (
                "This process is running the code baked into its image, not the "
                "checkout mounted beside it. It does not hot-reload, so a restart "
                "(or a rebuild, if dependencies moved) is needed before the mounted "
                "code takes effect. Symptoms look like a smaller deployment: an "
                "older protocol version and missing tools."
            )
        elif not self.build_sha:
            # Said plainly rather than left as a silent pass: without a build
            # SHA this check cannot do its job, and that is worth knowing
            # before somebody relies on it.
            out["detail"] = (
                f"No {BUILD_SHA_ENV} was baked into this image, so staleness cannot "
                "be detected. Build with "
                "--build-arg METAFORGE_BUILD_SHA=$(git rev-parse HEAD)."
            )
        return out


def _env_sha() -> str | None:
    value = (os.environ.get(BUILD_SHA_ENV) or "").strip()
    return value or None


def _git_head(root: Path) -> str | None:
    """HEAD of the checkout at ``root``, without requiring the git binary.

    Reads ``.git`` directly: the container may not have git installed, and a
    diagnostic that depends on a tool the image might omit is a diagnostic
    that silently stops working.
    """
    git_dir = root / ".git"
    try:
        if git_dir.is_file():
            # A worktree: `.git` is a file pointing at the real directory.
            pointer = git_dir.read_text().strip()
            if pointer.startswith("gitdir:"):
                git_dir = Path(pointer.split(":", 1)[1].strip())
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            ref_path = git_dir / ref
            if ref_path.exists():
                return ref_path.read_text().strip()
            # Packed refs: fall back to the binary if it happens to exist.
            packed = git_dir / "packed-refs"
            if packed.exists():
                for line in packed.read_text().splitlines():
                    if line.endswith(f" {ref}"):
                        return line.split(" ", 1)[0]
            return _git_binary(root)
        return head or None
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
        logger.debug("code_version_source_unreadable", root=str(root), error=str(exc))
        return None


def _git_binary(root: Path) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    sha = out.stdout.strip()
    return sha or None


def code_version(*, reloads: bool = False) -> CodeVersion:
    """What this process is running.

    ``reloads`` says whether the process picks up the mounted source by
    itself: true for the gateway (uvicorn's reloader supervises it), false for
    the MCP sidecar, which calls uvicorn programmatically and so keeps running
    whatever its image holds.
    """
    root = Path(os.environ.get(_SOURCE_ROOT_ENV) or _DEFAULT_SOURCE_ROOT)
    source = _git_head(root) if root.exists() else None
    if source is None and root != Path.cwd():
        # Running outside a container: the working tree is the checkout.
        source = _git_head(Path.cwd())
    return CodeVersion(build_sha=_env_sha(), source_sha=source, reloads=reloads)
