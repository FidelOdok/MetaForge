"""Nothing is tagged for deployment unless CI passed on that commit.

commit-image-tags.yml gives the images a commit's tag only after CI on main
passed, and every deployment locks to such a tag.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_nothing_is_tagged_for_deployment_without_ci() -> None:
    tags = (WORKFLOWS / "commit-image-tags.yml").read_text()
    assert '.path == ".github/workflows/ci.yml"' in tags
    assert "CI on ${SHA} did not pass" in tags
