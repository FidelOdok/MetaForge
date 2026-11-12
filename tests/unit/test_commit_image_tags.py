"""Every main commit names the whole image stack (FORGE-852).

Images are rebuilt only when their inputs change, so before this a commit SHA
named a gateway image and, usually, nothing else. A deployment that pinned
``METAFORGE_ADAPTER_TAG`` to a commit found no adapter carrying it, and Cloud's
deploy failed with ``metaforge-gateway:<sha>: not found`` because the gateway
was only tagged ``:latest`` and ``:VERSION``.

``scripts/ci/tag_commit_images.sh`` gives every image a ``:<sha>`` tag: built
at that commit, or carried from the nearest tagged ancestor. These tests run
the script against a fake registry (a JSON file behind stub ``docker`` and
``cosign`` binaries) and a real throwaway git history. No network, no Docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "tag_commit_images.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "commit-image-tags.yml"
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name == "nt",
    reason="needs bash and git",
)

GW = "ghcr.io/fidelodok/metaforge-gateway"
CQ = "ghcr.io/fidelodok/metaforge-cadquery-adapter"

COMPOSE = textwrap.dedent(
    """\
    services:
      gateway:
        image: ghcr.io/fidelodok/metaforge-gateway:${METAFORGE_GATEWAY_TAG:-latest}
      cadquery:
        image: ghcr.io/fidelodok/metaforge-cadquery-adapter:${METAFORGE_ADAPTER_TAG:-latest}
      seaweedfs:
        image: ghcr.io/fidelodok/metaforge-seaweedfs:${METAFORGE_SEAWEEDFS_TAG:-4.48}
    """
)

# `docker buildx imagetools inspect REF --format ...` prints the digest;
# `docker buildx imagetools create --tag REF SRC@DIGEST` records the tag.
FAKE_DOCKER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    reg = os.environ["FAKE_REGISTRY"]
    data = json.load(open(reg))
    a = sys.argv[1:]
    assert a[:2] == ["buildx", "imagetools"], a
    if a[2] == "inspect":
        d = data["tags"].get(a[3])
        if not d:
            sys.exit(1)
        print(json.dumps(d))
    elif a[2] == "create":
        tag, src = a[a.index("--tag") + 1], a[-1]
        data["tags"][tag] = src.split("@", 1)[1]
        json.dump(data, open(reg, "w"))
    """
)

FAKE_COSIGN = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    data = json.load(open(os.environ["FAKE_REGISTRY"]))
    ref = sys.argv[-1]
    sys.exit(0 if ref.split("@", 1)[1] in data["signed"] else 1)
    """
)


@pytest.fixture
def env(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(COMPOSE)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    shas = []
    for i in range(3):
        (repo / "f").write_text(str(i))
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", f"c{i}"], check=True)
        shas.append(
            subprocess.run(
                [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
        )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("cosign", FAKE_COSIGN)):
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)
    registry = tmp_path / "registry.json"

    def run(sha: str, tags: dict[str, str], signed: list[str], **extra: str):
        registry.write_text(json.dumps({"tags": tags, "signed": signed}))
        e = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_REGISTRY": str(registry),
            "REPO": "FidelOdok/MetaForge",
            **extra,
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT), sha], cwd=repo, env=e, capture_output=True, text=True
        )
        return proc, json.loads(registry.read_text())["tags"]

    return shas, run


def test_an_image_not_rebuilt_carries_its_nearest_ancestor_digest(env) -> None:
    (c0, c1, c2), run = env
    proc, tags = run(
        c2,
        {f"{GW}:{c2}": "sha256:gw2", f"{CQ}:{c0}": "sha256:cq0", f"{CQ}:latest": "sha256:cq0"},
        ["sha256:gw2", "sha256:cq0"],
    )
    assert proc.returncode == 0, proc.stderr
    assert tags[f"{CQ}:{c2}"] == "sha256:cq0"
    assert tags[f"{GW}:{c2}"] == "sha256:gw2"


def test_the_nearest_ancestor_wins_not_latest(env) -> None:
    """A newer commit may already have moved :latest; the ancestor is the truth."""
    (c0, c1, c2), run = env
    proc, tags = run(
        c1,
        {
            f"{GW}:{c1}": "sha256:gw1",
            f"{CQ}:{c0}": "sha256:cq0",
            f"{CQ}:latest": "sha256:cq-from-a-later-commit",
        },
        ["sha256:gw1", "sha256:cq0", "sha256:cq-from-a-later-commit"],
    )
    assert proc.returncode == 0, proc.stderr
    assert tags[f"{CQ}:{c1}"] == "sha256:cq0"


def test_pinned_upstream_images_are_left_alone(env) -> None:
    (c0, c1, c2), run = env
    proc, tags = run(
        c2,
        {f"{GW}:{c2}": "sha256:gw2", f"{CQ}:latest": "sha256:cq"},
        ["sha256:gw2", "sha256:cq"],
    )
    assert proc.returncode == 0, proc.stderr
    assert not any("seaweedfs" in t for t in tags)


def test_an_unsigned_digest_is_never_tagged(env) -> None:
    (c0, c1, c2), run = env
    proc, tags = run(
        c2,
        {f"{GW}:{c2}": "sha256:gw2", f"{CQ}:{c1}": "sha256:unsigned"},
        ["sha256:gw2"],
    )
    assert proc.returncode != 0
    assert f"{CQ}:{c2}" not in tags
    assert "UNSIGNED" in proc.stderr


def test_a_required_image_is_never_carried(env) -> None:
    """A commit whose gateway did not build must not get a stack tag with an old one."""
    (c0, c1, c2), run = env
    proc, tags = run(
        c2,
        {f"{GW}:{c1}": "sha256:gw1", f"{CQ}:latest": "sha256:cq"},
        ["sha256:gw1", "sha256:cq"],
        REQUIRE_BUILT="metaforge-gateway",
    )
    assert proc.returncode != 0
    assert f"{GW}:{c2}" not in tags
    assert "NOT BUILT" in proc.stderr


def test_the_gateway_is_tagged_with_its_commit_when_published() -> None:
    job = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]["docker-publish"]
    tags_step = next(s for s in job["steps"] if s.get("id") == "tags")
    assert "${image}:${GITHUB_SHA}" in tags_step["run"]


def test_the_workflow_runs_one_commit_at_a_time_in_order() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert data["concurrency"]["cancel-in-progress"] is False
    # Ancestors are found through git history, so the checkout must have it.
    checkout = data["jobs"]["tag"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout")
    assert int(checkout["with"]["fetch-depth"]) >= 100
    tag_step = data["jobs"]["tag"]["steps"][-1]
    assert "metaforge-gateway" in tag_step["env"]["REQUIRE_BUILT"]
