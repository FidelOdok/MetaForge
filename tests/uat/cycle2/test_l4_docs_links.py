"""UAT-C2-L4 — Integration docs render & links resolve (MET-341, MET-342).

Acceptance bullets validated:

* MET-341: ``docs/integrations/claude-code.md`` exists and contains the
  expected step headings.
* MET-342: ``docs/integrations/codex.md`` exists with the HTTP/SSE shape.
* Both walkthroughs cross-link to ``mcp-config-examples.md`` and to each
  other.
* All relative-path internal links resolve to actual files in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.uat.conftest import REPO_ROOT, assert_validates

pytestmark = [pytest.mark.uat]


CLAUDE_CODE_DOC = REPO_ROOT / "docs" / "integrations" / "claude-code.md"
CODEX_DOC = REPO_ROOT / "docs" / "integrations" / "codex.md"
EXAMPLES_DOC = REPO_ROOT / "docs" / "integrations" / "mcp-config-examples.md"


# ---------------------------------------------------------------------------
# MET-341 — Claude Code walkthrough
# ---------------------------------------------------------------------------


def test_met341_claude_code_doc_exists_with_expected_sections() -> None:
    assert_validates(
        "MET-341",
        "docs/integrations/claude-code.md exists and is non-trivial",
        CLAUDE_CODE_DOC.exists() and CLAUDE_CODE_DOC.stat().st_size > 1000,
    )
    body = CLAUDE_CODE_DOC.read_text(encoding="utf-8")
    for required in ("Prerequisites", ".mcp.json", "/mcp", "Troubleshooting"):
        assert_validates(
            "MET-341",
            f"claude-code.md mentions '{required}'",
            required in body,
        )


# ---------------------------------------------------------------------------
# MET-342 — Codex / generic walkthrough
# ---------------------------------------------------------------------------


def test_met342_codex_doc_exists_with_http_examples() -> None:
    assert_validates(
        "MET-342",
        "docs/integrations/codex.md exists and is non-trivial",
        CODEX_DOC.exists() and CODEX_DOC.stat().st_size > 1000,
    )
    body = CODEX_DOC.read_text(encoding="utf-8")
    for required in ("--transport http", "Bearer", "/health", "/mcp"):
        assert_validates(
            "MET-342",
            f"codex.md mentions '{required}'",
            required in body,
        )


# ---------------------------------------------------------------------------
# Cross-linking
# ---------------------------------------------------------------------------


def test_met341_342_docs_cross_link_examples_doc() -> None:
    for met_id, path in [("MET-341", CLAUDE_CODE_DOC), ("MET-342", CODEX_DOC)]:
        body = path.read_text(encoding="utf-8")
        assert_validates(
            met_id,
            f"{path.name} cross-links to mcp-config-examples.md",
            "mcp-config-examples.md" in body,
        )


# ---------------------------------------------------------------------------
# Relative-link integrity
# ---------------------------------------------------------------------------


_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


@pytest.mark.parametrize(
    "doc_path",
    [CLAUDE_CODE_DOC, CODEX_DOC, EXAMPLES_DOC],
    ids=["claude-code", "codex", "mcp-config-examples"],
)
def test_met341_342_relative_links_resolve(doc_path: Path) -> None:
    body = doc_path.read_text(encoding="utf-8")
    broken: list[str] = []
    for match in _LINK_RE.finditer(body):
        target = match.group(1).split("#")[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        # Resolve relative to the doc's directory
        candidate = (doc_path.parent / target).resolve()
        if not candidate.exists():
            broken.append(target)
    assert_validates(
        "MET-341/342",
        f"all relative markdown links in {doc_path.name} resolve",
        not broken,
        f"broken links: {broken}",
    )
