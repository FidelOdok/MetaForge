"""The gateway image has to be buildable from its own build context (FORGE-648).

Adding ``readme`` and ``license-files`` to ``pyproject.toml`` broke the image
publish and nothing caught it until main was already merged. The repo's own
checks could not: CI installs the package from a full checkout, where every
file is there. The image builds from a narrow context, and ``.dockerignore``
excluded ``*.md`` and ``LICENSE`` -- so hatchling died generating metadata
with::

    OSError: Readme file does not exist: README.md

The failure mode is specific and recurring: a field in ``pyproject.toml``
names a file, and the image's build context does not have it. So this
asserts the relationship rather than the two filenames, and keeps holding
when someone adds a third licence file.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "Dockerfile"


def packaging_metadata_files() -> list[str]:
    """Files pyproject names, which hatchling must read at build time."""
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    named: list[str] = []
    readme = project.get("readme")
    if isinstance(readme, str):
        named.append(readme)
    elif isinstance(readme, dict) and "file" in readme:
        named.append(readme["file"])
    named.extend(project.get("license-files", []))
    licence = project.get("license")
    if isinstance(licence, dict) and "file" in licence:
        named.append(licence["file"])
    return named


NAMED = packaging_metadata_files()


def test_pyproject_names_some_metadata_files() -> None:
    # Without this the parametrised tests below would pass by being empty.
    assert NAMED, "pyproject names no readme or licence files -- is the parsing still right?"


@pytest.mark.parametrize("name", NAMED)
def test_the_file_exists(name: str) -> None:
    assert (REPO / name).is_file(), f"pyproject names {name}, which is not in the repo"


@pytest.mark.parametrize("name", NAMED)
def test_the_build_context_is_allowed_to_see_it(name: str) -> None:
    """``.dockerignore`` must not exclude it, or COPY silently has nothing."""
    lines = [
        line.strip()
        for line in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    # Later rules win, so walk forwards and keep the last verdict.
    excluded = False
    for line in lines:
        pattern = line[1:] if line.startswith("!") else line
        if _matches(pattern, name):
            excluded = not line.startswith("!")
    assert not excluded, (
        f".dockerignore excludes {name}, which pyproject.toml needs at build "
        f"time. Add `!{name}` after the rule that excludes it."
    )


# A `pip install` target that means "this project": `.`, `.[extras]`, or
# either of those quoted. Counting whole-file occurrences instead is how the
# first version of this test let a mutation through -- it matched the
# `".[gateway,...]"` stage and missed the bare `--no-deps .` one, so the two
# totals came out equal while one stage was genuinely broken.
LOCAL_TARGET = re.compile(r'^"?\.(\[[^\]]*\])?"?$')


def _stages(text: str) -> list[str]:
    """The Dockerfile split per build stage, line continuations joined."""
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    parts = re.split(r"^FROM ", joined, flags=re.MULTILINE)
    return [p for p in parts[1:] if p.strip()]


def _installs_the_project(stage: str) -> bool:
    for line in stage.splitlines():
        line = line.strip()
        if not line.startswith("RUN ") or "pip install" not in line:
            continue
        after = line.split("pip install", 1)[1]
        if any(LOCAL_TARGET.match(tok) for tok in after.split()):
            return True
    return False


def test_some_stage_installs_the_project() -> None:
    # Guard on the guard: if the detection stops matching, the test below
    # would pass by finding nothing to check.
    assert any(_installs_the_project(s) for s in _stages(DOCKERFILE.read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", NAMED)
def test_every_stage_that_installs_the_package_copies_it(name: str) -> None:
    for i, stage in enumerate(_stages(DOCKERFILE.read_text(encoding="utf-8")), start=1):
        if not _installs_the_project(stage):
            continue
        copied = re.search(rf"^COPY [^\n]*\b{re.escape(name)}\b", stage, re.MULTILINE)
        assert copied, (
            f"Dockerfile stage {i} ({stage.splitlines()[0].strip()}) installs "
            f"the project but never COPYs {name}, which pyproject.toml names. "
            f'The build fails with "Readme file does not exist" or the '
            f"licence-file equivalent."
        )


def _matches(pattern: str, name: str) -> bool:
    """Good enough for the flat, top-level patterns this file uses."""
    from fnmatch import fnmatch

    return fnmatch(name, pattern) or pattern == name


def _stage(name: str) -> str:
    for stage in _stages(DOCKERFILE.read_text(encoding="utf-8")):
        if re.match(rf"\S+ AS {name}\b", stage.splitlines()[0].strip(), re.IGNORECASE):
            return stage
    raise AssertionError(f"no '{name}' stage in the Dockerfile")


def test_the_dependency_layer_never_sees_the_stamped_version() -> None:
    # FORGE-715: the release workflow stamps the version into pyproject.toml
    # before building. Copied straight into the deps stage it changed the layer
    # every build, so every release reinstalled every dependency.
    deps = _stage("deps")
    assert not re.search(r"^COPY (?!--from)[^\n]*\bpyproject\.toml\b", deps, re.MULTILINE)
    assert re.search(r"^COPY --from=manifest [^\n]*\bpyproject\.toml\b", deps, re.MULTILINE)
    manifest = _stage("manifest")
    assert re.search(r"stamp_version\.py 0\.0\.0\b", manifest)


def test_the_runtime_stage_still_installs_the_stamped_package() -> None:
    # /health reads the installed version, so the real pyproject.toml must
    # still reach a `pip install --no-deps .`.
    runtime = _stage("runtime")
    assert re.search(r"^COPY (?!--from)[^\n]*\bpyproject\.toml\b", runtime, re.MULTILINE)
    assert "pip install --no-cache-dir --no-deps ." in runtime
