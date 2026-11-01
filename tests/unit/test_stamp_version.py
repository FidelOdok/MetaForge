"""Tests for scripts/stamp_version.py (FORGE-648).

The release workflow tags the repository and then builds an image from the
checkout. Until this script existed, nothing carried the tag into the package,
so the gateway reported the committed literal rather than the release it came
from.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts.stamp_version import main, normalise, stamp

PYPROJECT = """\
[build-system]
requires = ["hatchling"]

[project]
name = "metaforge-hw"
version = "0.3.0"
description = "something"

[tool.poetry.dependencies]
version = "ignored"
"""


class TestNormalise:
    def test_it_drops_the_tag_prefix(self) -> None:
        """Releases are tagged `v0.3.617`; metadata carries no `v`."""
        assert normalise("v0.3.617") == "0.3.617"
        assert normalise("0.3.617") == "0.3.617"

    def test_a_sha_marks_a_build_after_the_release(self) -> None:
        """A `:latest` image built past a tag is not that tag. The local
        segment sorts after it and cannot be read as the release."""
        assert normalise("v0.3.617", sha="64bdb1633bf658bd") == "0.3.617+g64bdb16"

    def test_it_strips_characters_a_local_segment_may_not_carry(self) -> None:
        assert normalise("0.3.617", sha="1a2-b3c.d") == "0.3.617+g1a2b3cd"

    def test_prerelease_shapes_survive(self) -> None:
        assert normalise("v1.0.0rc1") == "1.0.0rc1"

    @pytest.mark.parametrize("bad", ["", "v", "not-a-version", "1.2.3 ; rm -rf /", "0.1.0_dev"])
    def test_it_refuses_what_is_not_a_version(self, bad: str) -> None:
        """Better to fail the release than stamp a string that makes the
        metadata unreadable."""
        with pytest.raises(ValueError):
            normalise(bad)

    def test_it_refuses_a_sha_with_nothing_usable_in_it(self) -> None:
        with pytest.raises(ValueError):
            normalise("0.3.617", sha="---")


class TestStamp:
    def test_it_replaces_the_project_version(self) -> None:
        stamped, previous = stamp(PYPROJECT, "0.3.617")
        assert previous == "0.3.0"
        assert tomllib.loads(stamped)["project"]["version"] == "0.3.617"

    def test_it_leaves_other_tables_alone(self) -> None:
        """Another table's `version` key is a different fact. Scanning the
        file for the first match would have rewritten this one."""
        stamped, _ = stamp(PYPROJECT, "0.3.617")
        assert tomllib.loads(stamped)["tool"]["poetry"]["dependencies"]["version"] == "ignored"

    def test_it_changes_nothing_else(self) -> None:
        stamped, _ = stamp(PYPROJECT, "0.3.617")
        before = tomllib.loads(PYPROJECT)
        after = tomllib.loads(stamped)
        before["project"].pop("version")
        after["project"].pop("version")
        assert before == after

    def test_it_says_so_when_there_is_no_version_to_stamp(self) -> None:
        with pytest.raises(LookupError):
            stamp("[project]\nname = 'x'\n", "0.3.617")


class TestMain:
    def test_it_writes_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "pyproject.toml"
        path.write_text(PYPROJECT, encoding="utf-8")
        assert main(["v0.3.617", "--path", str(path)]) == 0
        assert tomllib.loads(path.read_text())["project"]["version"] == "0.3.617"

    def test_a_bad_version_fails_without_touching_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "pyproject.toml"
        path.write_text(PYPROJECT, encoding="utf-8")
        assert main(["nonsense", "--path", str(path)]) == 2
        assert path.read_text() == PYPROJECT

    def test_the_real_pyproject_can_be_stamped(self) -> None:
        """The shape this exists to edit is the repo's own file, so an edit
        there that stopped matching would otherwise only show up in a release."""
        repo = Path(__file__).resolve().parents[2]
        text = (repo / "pyproject.toml").read_text(encoding="utf-8")
        stamped, previous = stamp(text, "9.9.9")
        assert previous
        assert tomllib.loads(stamped)["project"]["version"] == "9.9.9"
