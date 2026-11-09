"""The engine page for an authored form must not vouch for a form it never analysed.

The engine's checks, verdict, cost, assembly and electronics blocks are computed
from the enclosure template. For an authored form that enclosure is a hidden
placeholder, so those blocks would read "15 of 16 checks pass" about a lamp they
never looked at. These tests pin that the page hides each of them and replaces the
enclosure's sliders with the form's own parameters (spec 23.6, IMG-FORM-008).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).parents[2] / "api_gateway" / "imagine" / "engine"
HTML = (ENGINE_DIR / "engine.html").read_text(encoding="utf-8")
CSS = (ENGINE_DIR / "engine.css").read_text(encoding="utf-8")
JS = (ENGINE_DIR / "engine.js").read_text(encoding="utf-8")

# Every block of the right-hand pane that is computed from the enclosure.
ENCLOSURE_BLOCKS = [
    "gateBlk",  # Readiness: gates per part
    "reqBlk",  # Requirements met
    "valBlk",  # Validation in the background
    "dfaBlk",  # Assembly
    "envBlk",  # Where it lives
    "verdictBlk",  # Manufacturing verdict
    "testBlk",  # Product tests
    "elecBlk",  # Electronics and wiring
    "rulesBlk",  # Checks
    "bomBlk",  # Bill of materials
    "partBlk",  # Part metrics
    "costBlk",  # Unit cost by process
    "handBlk",  # Hand off
]


@pytest.mark.parametrize("block", ENCLOSURE_BLOCKS)
def test_each_enclosure_analysis_block_is_hidden_for_an_authored_form(block: str) -> None:
    assert f'id="{block}"' in HTML, f"{block} has no id in engine.html"
    rules = re.findall(r"html\[data-authored\][^{]*\{[^}]*display\s*:\s*none", CSS)
    assert any(f"#{block}" in rule for rule in rules), f"{block} is not hidden under data-authored"


def test_the_enclosure_sliders_are_replaced_by_the_forms_own_parameters() -> None:
    rule = (
        r"html\[data-authored\]\s+#graphPane\s*>\s*\*:not\(\.fg-authored-panel\)"
        r"[^{]*\{[^}]*display\s*:\s*none"
    )
    assert re.search(rule, CSS), "the graph pane still shows the enclosure's sliders"
    assert "html[data-authored] #graphPane .fg-authored-panel" in CSS


def test_the_engine_marks_the_page_authored_and_skips_the_enclosure_panel() -> None:
    assert "setAttribute('data-authored'" in JS
    assert re.search(r"function renderPanel\(ev\)\{\s*if\(AUTHORED\)", JS), (
        "renderPanel must not run the enclosure's checks for an authored form"
    )


def test_the_parameters_mount_in_the_left_pane_and_the_summary_in_the_right() -> None:
    assert "host:document.getElementById('graphPane')" in JS
    assert "summaryHost:document.getElementById('dfm')" in JS


# -- the docs say what the code does ----------------------------------------------------

DOCS = Path(__file__).parents[2] / "docs" / "imagine-stage.md"


def _authored_docs() -> str:
    text = DOCS.read_text(encoding="utf-8")
    assert "## Authored forms" in text, "docs/imagine-stage.md has no Authored forms section"
    start = text.index("## Authored forms")
    nxt = text.find("\n## ", start + 5)
    return text[start : nxt if nxt != -1 else len(text)]


def test_the_docs_state_the_budgets_the_checker_enforces() -> None:
    from api_gateway.imagine.formgraph import authored

    section = _authored_docs().replace(",", "").replace(" ", "")
    for number in (
        authored.MAX_PARTS,
        authored.MAX_PART_CODE_BYTES,
        authored.MAX_TOTAL_CODE_BYTES,
        authored.MAX_PART_TRIANGLES,
        authored.MAX_TOTAL_TRIANGLES,
    ):
        assert str(number) in section, number


def test_the_docs_name_the_findings_the_script_and_the_sandbox_properties() -> None:
    section = _authored_docs()
    for needle in (
        "form.enclosure_checks",
        "form.confidence_mix",
        "scripts/imagine_look.py",
        "window.FG_AUTHORED",
        "one worker per part",
        "never passed to `eval`",
        "worker-src blob:",
        "not assessed",
    ):
        assert needle in section, needle


def test_the_docs_do_not_claim_the_page_checks_an_authored_form() -> None:
    section = _authored_docs()
    assert "never reads as a pass" in section
    assert "15 of 16" not in section


def test_the_docs_have_no_em_dashes() -> None:
    assert "—" not in DOCS.read_text(encoding="utf-8")
