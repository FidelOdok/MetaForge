"""The things a public repository has to get right (FORGE-648).

Every one of these guards a failure that was live in the repository while
it was already public:

* ``LICENSE``, ``CONTRIBUTING.md`` and ``CODE_OF_CONDUCT.md`` were all
  **zero bytes** -- committed as placeholders at the first commit and never
  filled -- while both plugin manifests declared ``Apache-2.0``. With no
  licence text the default is all rights reserved, so nobody could legally
  fork or contribute, and the manifests contradicted that.
* The local plugin's documented first step was ``pip install metaforge``.
  That name on PyPI belongs to an unrelated job-shop scheduling library by
  someone else, so the instruction installed a stranger's package and left
  ``metaforge-mcp`` nowhere on PATH.
* Two install scripts, ``install.sh`` and ``onboarding.sh``, where the
  obvious name did the least.

These are cheap to assert and expensive to notice by eye, which is exactly
the combination that let them sit there.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

COMMUNITY_FILES = ["LICENSE", "NOTICE", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"]


class TestTheRepositoryCanBeUsed:
    """A licence nobody can read is the same as no licence."""

    @pytest.mark.parametrize("name", COMMUNITY_FILES)
    def test_the_file_exists_and_says_something(self, name: str) -> None:
        path = REPO / name
        assert path.is_file(), f"{name} is missing"
        # 500 bytes, not 0: the point is to catch a placeholder, and every
        # one of these was literally empty on a public repository.
        assert len(path.read_text(encoding="utf-8").strip()) > 500, (
            f"{name} is empty or a stub. It was 0 bytes on a public "
            f"repository for months -- do not let it go back."
        )

    def test_the_licence_is_the_real_apache_text(self) -> None:
        text = (REPO / "LICENSE").read_text(encoding="utf-8")
        for phrase in (
            "Apache License",
            "Version 2.0, January 2004",
            "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION",
            'distributed on an "AS IS" BASIS',
        ):
            assert phrase in text, f"LICENSE does not look like Apache-2.0: missing {phrase!r}"

    def test_every_manifest_agrees_with_the_licence_we_ship(self) -> None:
        # The plugin manifests claimed Apache-2.0 while LICENSE was empty.
        # Whatever the answer is, it has to be one answer.
        declared = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        project_licence = declared["project"]["license"]
        assert project_licence == "Apache-2.0"

        manifests = sorted(REPO.glob("integrations/*/.claude-plugin/plugin.json"))
        assert manifests, "no plugin manifests found -- this test is checking nothing"
        for manifest in manifests:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            assert payload.get("license") == project_licence, (
                f"{manifest.relative_to(REPO)} declares "
                f"{payload.get('license')!r}, pyproject declares {project_licence!r}"
            )

    def test_the_code_of_conduct_names_someone_to_report_to(self) -> None:
        text = (REPO / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
        assert "[INSERT CONTACT METHOD]" not in text, (
            "the Contributor Covenant's contact placeholder is still in place"
        )
        assert "@" in text, "no contact address in the Code of Conduct"


class TestTheInstallInstructionsPointAtUs:
    """An instruction that installs someone else's package is worse than none."""

    # `metaforge` exactly -- not `metaforge-hw`, `metaforge-capture`,
    # `metaforge[neo4j]` or a longer word starting the same way.
    BARE_NAME = re.compile(r"pip install\s+[\"']?metaforge(?![-\w\[])")

    def _instruction_files(self) -> list[Path]:
        files = [
            p
            for p in REPO.rglob("*.md")
            if "node_modules" not in p.parts
            and ".venv" not in p.parts
            and "docs-site/build" not in p.as_posix()
        ]
        files.append(REPO / ".claude-plugin" / "marketplace.json")
        return [p for p in files if p.is_file()]

    def test_the_search_finds_files_to_check(self) -> None:
        # Without this the test below passes by checking nothing.
        assert len(self._instruction_files()) > 50

    def test_nothing_tells_people_to_install_the_name_we_do_not_own(self) -> None:
        offenders = [
            p.relative_to(REPO).as_posix()
            for p in self._instruction_files()
            if self.BARE_NAME.search(p.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert not offenders, (
            "these tell people to `pip install metaforge`, which is an "
            f"unrelated project on PyPI: {offenders}"
        )

    def test_the_distribution_is_not_named_after_a_package_someone_else_owns(self) -> None:
        declared = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        assert declared["project"]["name"] != "metaforge"

    def test_the_local_plugin_still_gets_its_entry_point(self) -> None:
        # Renaming the distribution must not drop the console script the
        # stdio plugin launches by name.
        declared = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        assert "metaforge-mcp" in declared["project"]["scripts"]

        local_manifest = (
            REPO / "integrations" / "claude-code-local" / ".claude-plugin" / "plugin.json"
        )
        manifest = json.loads(local_manifest.read_text(encoding="utf-8"))
        assert manifest["mcpServers"]["metaforge"]["command"] == "metaforge-mcp"


class TestThereIsOneInstaller:
    """Two scripts, and the obvious name did the least (FORGE-648)."""

    # Every script the docs tell people to run by bare path. `bash foo.sh`
    # does not need the bit; `./foo.sh` and `scripts/foo.sh` do.
    DIRECTLY_INVOKED = ["install.sh", "build_forge_binary.sh", "host_update.sh"]

    def test_the_installer_is_where_people_will_look(self) -> None:
        assert (REPO / "scripts" / "install.sh").is_file()

    @pytest.mark.parametrize("name", DIRECTLY_INVOKED)
    def test_a_script_people_run_by_path_is_executable(self, name: str) -> None:
        """...in git's index, which is the only copy a clone gets.

        Asserting ``Path.stat().st_mode`` instead is how this first shipped,
        and it was useless: this repo is developed on a ``/mnt/c`` mount
        where ``core.fileMode`` is false, so a local ``chmod +x`` is real on
        disk and invisible to git. The test passed on the machine that made
        the change and failed in CI, where the clone had mode 644 -- and
        ``./scripts/install.sh`` is "Permission denied" for everyone who
        clones. Every ``.sh`` in ``scripts/`` was 644 at the time.
        """
        mode = subprocess.run(
            ["git", "ls-tree", "HEAD", f"scripts/{name}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert mode, f"scripts/{name} is not tracked"
        assert mode[0] == "100755", (
            f"scripts/{name} is mode {mode[0]} in git, so a fresh clone cannot "
            f"run it by path. Fix with: git update-index --chmod=+x scripts/{name}"
        )

    def test_the_second_installer_is_gone(self) -> None:
        assert not (REPO / "scripts" / "onboarding.sh").exists(), (
            "onboarding.sh is back -- there is meant to be one installer"
        )

    def test_it_does_the_whole_job_and_the_binary_on_its_own(self) -> None:
        text = (REPO / "scripts" / "install.sh").read_text(encoding="utf-8")
        assert "--cli-only" in text, "the binary-only path (the old install.sh) is missing"
        assert "install_forge_cli" in text
        # The full install has to still be in here, not just the binary.
        assert "docker compose" in text or "DC=(docker compose)" in text

    def test_it_says_which_shell_it_needs(self) -> None:
        # The retired install.sh was POSIX sh, documented as `curl ... | sh`,
        # and that one-liner is published. Running this one that way must
        # produce an instruction, not a syntax error from the middle.
        text = (REPO / "scripts" / "install.sh").read_text(encoding="utf-8")
        assert 'if [ -z "${BASH_VERSION:-}" ]; then' in text
        guard = text.index('if [ -z "${BASH_VERSION:-}" ]; then')
        assert guard < text.index("set -euo pipefail"), (
            "the bash guard must come before any bash-only syntax"
        )

    def test_the_documented_one_liner_matches_the_script_that_exists(self) -> None:
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        assert "scripts/install.sh | bash" in readme
        assert "onboarding.sh" not in readme
