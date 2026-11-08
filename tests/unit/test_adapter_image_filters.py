"""Each published image is rebuilt when, and only when, its inputs change
(FORGE-838).

`adapter-images.yml` used to rebuild all twelve images on any change under
mcp_core/, tool_registry/ or observability/: a fix to the twin adapter, which
no image carries, cost about nineteen minutes of runner time. It now asks
dorny/paths-filter which images changed, using
`.github/adapter-image-filters.yml`, and the adapter Dockerfiles copy only the
shared modules they import.

The risk of narrowing is the opposite failure: a Dockerfile copies a file
that no filter watches, so a change to it never reaches the published image.
These tests check, against the real files in the repository, that every file
an adapter Dockerfile copies is covered by that adapter's filter, that each
filter covers its own folder (and so its Dockerfile) and the CI files, and
that the tool downloads in the Dockerfiles retry and are checksummed.

Structural checks only; no Docker, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
FILTERS = REPO_ROOT / ".github" / "adapter-image-filters.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "adapter-images.yml"

#: Filter keys that are not tool adapters, with their own build folder.
SERVICE_FOLDERS = {"occt": "tools/occt-converter", "dashboard": "dashboard"}

#: Changing any of these rebuilds every image.
CI_FILES = [
    ".github/workflows/adapter-images.yml",
    ".github/adapter-image-filters.yml",
    ".github/actions/install-cosign/action.yml",
    "scripts/adapter_import_smoke.py",
]


def _flatten(value: object) -> list[str]:
    if isinstance(value, list):
        return [p for item in value for p in _flatten(item)]
    return [str(value)]


def _filters() -> dict[str, list[str]]:
    data = yaml.safe_load(FILTERS.read_text(encoding="utf-8"))
    return {k: _flatten(v) for k, v in data.items() if not k.startswith("_")}


def _adapters() -> list[str]:
    return [k for k in _filters() if k not in SERVICE_FOLDERS]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """picomatch semantics for the subset the filter file uses."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_to_regex(p).match(path) for p in patterns)


def _copied_files(dockerfile: Path) -> list[str]:
    """Every repository file the Dockerfile's COPY lines take from the context."""
    files: list[str] = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0] != "COPY":
            continue
        args = [a for a in parts[1:] if not a.startswith("--")]
        if any(a.startswith("--from") for a in parts[1:]):
            continue
        for src in args[:-1]:
            if src.endswith("/"):
                matched = [p for p in (REPO_ROOT / src).rglob("*") if p.is_file()]
            else:
                matched = [p for p in REPO_ROOT.glob(src) if p.is_file()]
            assert matched, f"{dockerfile}: COPY {src} matches nothing"
            files += [p.relative_to(REPO_ROOT).as_posix() for p in matched]
    return [f for f in files if "__pycache__" not in f]


@pytest.mark.parametrize("image", sorted(_filters()))
class TestEveryImageFilter:
    def test_it_watches_its_own_folder_and_dockerfile(self, image: str) -> None:
        folder = SERVICE_FOLDERS.get(image, f"tool_registry/tools/{image}")
        patterns = _filters()[image]
        assert f"{folder}/**" in patterns, f"{image} does not watch {folder}/"
        assert _matches(f"{folder}/Dockerfile", patterns)
        assert (REPO_ROOT / folder / "Dockerfile").is_file()

    def test_it_watches_the_ci_files(self, image: str) -> None:
        patterns = _filters()[image]
        for path in CI_FILES:
            assert _matches(path, patterns), f"{image} is not rebuilt when {path} changes"


@pytest.mark.parametrize("adapter", sorted(_adapters()))
class TestAdapterFiltersCoverWhatTheImageCopies:
    def test_every_copied_file_is_watched(self, adapter: str) -> None:
        """The property narrowing can break: a file in the image whose change
        does not rebuild it."""
        dockerfile = REPO_ROOT / "tool_registry" / "tools" / adapter / "Dockerfile"
        patterns = _filters()[adapter]
        unwatched = [f for f in _copied_files(dockerfile) if not _matches(f, patterns)]
        assert not unwatched, f"{adapter} copies files no filter watches: {unwatched[:10]}"

    def test_it_copies_the_shared_runtime(self, adapter: str) -> None:
        """Every adapter runs the MCP server, which needs these."""
        copied = set(_copied_files(REPO_ROOT / "tool_registry" / "tools" / adapter / "Dockerfile"))
        for needed in (
            "mcp_core/protocol.py",
            "observability/tracing.py",
            "tool_registry/__init__.py",
            "tool_registry/mcp_server/server.py",
            "tool_registry/tools/__init__.py",
            f"tool_registry/tools/{adapter}/adapter.py",
        ):
            assert needed in copied, f"{adapter} image lacks {needed}"

    def test_unrelated_code_does_not_rebuild_it(self, adapter: str) -> None:
        """The case that cost nineteen minutes: a change to an adapter no
        image carries, and to the harness plugin files in mcp_core."""
        patterns = _filters()[adapter]
        for path in (
            "tool_registry/tools/twin/adapter.py",
            "mcp_core/plugin_skills/x/SKILL.md",
            "observability/dashboards/x.json",
        ):
            assert not _matches(path, patterns), f"{adapter} rebuilds on {path}"


def test_every_tool_adapter_dockerfile_is_a_pull_request_trigger() -> None:
    """A pull request that changes a Dockerfile builds it without pushing."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = data.get("on", data.get(True))
    paths = on["pull_request"]["paths"]
    for adapter in _adapters():
        assert _matches(f"tool_registry/tools/{adapter}/Dockerfile", paths), adapter
    for folder in SERVICE_FOLDERS.values():
        assert _matches(f"{folder}/Dockerfile", paths), folder
    for path in CI_FILES:
        assert _matches(path, paths), path


def test_the_workflow_reads_this_filter_file() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "filters: .github/adapter-image-filters.yml" in text
    assert re.search(r"dorny/paths-filter@[0-9a-f]{40}", text), "paths-filter is not pinned"


class TestToolDownloadsRetryAndAreChecked:
    """GitHub release downloads 504'd and failed the kicad and firmware builds."""

    @pytest.mark.parametrize("adapter", ["kicad", "firmware", "blender"])
    def test_every_curl_retries_on_any_error(self, adapter: str) -> None:
        text = (REPO_ROOT / "tool_registry" / "tools" / adapter / "Dockerfile").read_text(
            encoding="utf-8"
        )
        # A curl invocation (flags follow), not the apt package of that name.
        curls = [m.group(0) for m in re.finditer(r"\bcurl -[^\n]*", text)]
        assert curls, adapter
        for call in curls:
            assert "--retry-all-errors" in call and "--retry " in call, call

    @pytest.mark.parametrize(
        ("adapter", "checksums"),
        [
            ("kicad", ["FREEROUTING_SHA256"]),
            ("firmware", ["RENODE_SHA256", "ESP_QEMU_SHA256"]),
            ("blender", ["BLENDER_SHA256"]),
        ],
    )
    def test_every_download_is_pinned_by_checksum(self, adapter: str, checksums: list[str]) -> None:
        text = (REPO_ROOT / "tool_registry" / "tools" / adapter / "Dockerfile").read_text(
            encoding="utf-8"
        )
        for name in checksums:
            assert re.search(rf"ARG {name}=[0-9a-f]{{64}}\b", text), name
            assert f'echo "${{{name}}}  ' in text and "sha256sum -c -" in text, name
