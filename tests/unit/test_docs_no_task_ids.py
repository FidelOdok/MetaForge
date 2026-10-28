"""The published docs and API reference carry no tracker ids.

`docs/` is the public site and `docs/reference/openapi.json` is generated from the
gateway's docstrings. A task id (FORGE-123, MET-123) is a pointer into a private
tracker, so it explains nothing to a reader; say what happened instead.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
# Upper case only: a lower-case `forge-530-revision-notes` is a literal Temporal patch
# name, and a file name keeps its history.
TASK_ID = re.compile(r"\b(?:FORGE|MET)-\d+")


def test_public_docs_carry_no_task_ids() -> None:
    found = [
        f"{path.relative_to(DOCS)}:{number}: {line.strip()[:90]}"
        for path in sorted(DOCS.rglob("*"))
        if path.suffix in {".md", ".mdx", ".json"}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if TASK_ID.search(line)
    ]
    assert not found, "task ids in public docs:\n" + "\n".join(found[:40])
