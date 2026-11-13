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

# The blocks of the right-hand pane that assert something about the placeholder enclosure the
# engine solves for every studio. For an authored form that enclosure is not the product, so each
# of these would read as assurance about a form it never analysed.
ASSERTS_ABOUT_THE_PLACEHOLDER = [
    "valBlk",  # Validation in the background: routing, heat and drop on the placeholder
    "dfaBlk",  # Assembly: a design-for-assembly index for the placeholder's screws
    "envBlk",  # Where it lives: the placeholder's internal temperature
    "verdictBlk",  # Manufacturing verdict
    "testBlk",  # Product tests: a drop test of the placeholder
    "elecBlk",  # Electronics and wiring: cables routed inside the placeholder
    "rulesBlk",  # Checks
    "bomBlk",  # Bill of materials: a top shell and a lid the form does not have
    "partBlk",  # Part metrics
    "costBlk",  # Unit cost by process
    "handBlk",  # Hand off
    "partReady",  # Readiness's parts table: "integrated" means fits the placeholder
]

# What carries the person's controls and records, and so must stay usable.
KEPT_FOR_THE_PERSON = [
    "gateBlk",  # Readiness: gate 1 says which assumptions to confirm
    "reqBlk",  # Requirements, the situations it must handle, and Confirm these assumptions
    "optBlk",  # Options: the part decisions
    "ctxBlk",  # Context: the parts and machines
    "mdlBlk",  # Models and scenarios
    "compBlk",  # What it does and what goes inside
]


def _hidden_ids() -> str:
    rules = re.findall(r"html\[data-authored\][^{]*\{[^}]*display\s*:\s*none", CSS)
    return " ".join(rules)


@pytest.mark.parametrize("block", ASSERTS_ABOUT_THE_PLACEHOLDER)
def test_each_block_that_asserts_about_the_placeholder_is_hidden_for_an_authored_form(
    block: str,
) -> None:
    assert f'id="{block}"' in HTML, f"{block} has no id in engine.html"
    assert f"#{block}" in _hidden_ids(), f"{block} is not hidden under data-authored"


@pytest.mark.parametrize("block", KEPT_FOR_THE_PERSON)
def test_each_block_that_carries_the_persons_controls_stays_visible(block: str) -> None:
    assert f'id="{block}"' in HTML, f"{block} has no id in engine.html"
    assert f"#{block}" not in _hidden_ids(), f"{block} is hidden: the person loses its controls"


def test_simple_level_shows_the_engines_controls_panel_and_engineer_shows_the_authored_graph() -> (
    None
):
    assert re.search(
        r'html\[data-authored\]\[data-level="simple"\]\s+#quick\s*\{[^}]*display\s*:\s*grid', CSS
    )
    assert re.search(
        r'html\[data-authored\]:not\(\[data-level="simple"\]\)\s+#authGraph\s*\{[^}]*display\s*:\s*flex',
        CSS,
    )
    # The enclosure's own graph editor stays hidden: it edits parameters a coded form lacks.
    ids = _hidden_ids()
    assert "#graphPane .gtools" in ids and "#graphPane #canvas" in ids


def test_the_engine_marks_the_page_authored_and_skips_the_enclosure_panel() -> None:
    assert "setAttribute('data-authored'" in JS
    assert re.search(r"function renderPanel\(ev\)\{\s*if\(AUTHORED\)", JS), (
        "renderPanel must not run the enclosure's checks for an authored form"
    )


def test_the_parameters_are_rendered_into_the_engines_own_controls_panel() -> None:
    assert "quick:document.getElementById('quick')" in JS
    assert "summaryHost:document.getElementById('dfm')" in JS
    assert re.search(r"function renderQuick\(ev\)\{\s*if\(AUTHORED\)return", JS), (
        "the engine's own panel would overwrite the authored one"
    )


def test_the_enclosure_is_not_given_a_shape_that_the_programme_view_needs() -> None:
    """A null shape breaks the Programme tab ("Connect a form to see the programme"). Keep it."""
    assert "ev.shape=null" not in JS and "ev.shape = null" not in JS


def test_a_requirement_is_never_marked_met_for_an_authored_form() -> None:
    assert re.search(r"const lab=AUTHORED\?\{[^}]*Not assessed", JS), (
        "the Met and At risk chips come from a check of the placeholder enclosure"
    )
    squashed = JS.replace(" ", "")
    assert "set('Requirements',AUTHORED?'notassessed':" in squashed, "the header says N of N met"


def test_the_gates_after_intent_are_never_green_for_an_authored_form() -> None:
    pattern = (
        r"if\(AUTHORED\)\{for\(let i=1;i<4;i\+\+\)\{g\[i\]\.ok=false;"
        r"g\[i\]\.msg='Not assessed for an authored form'"
    )
    assert re.search(pattern, JS), "gates 2 to 4 are decided by the enclosure analysis"


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


def test_the_look_tool_is_documented_where_the_other_imagine_tools_are() -> None:
    root = Path(__file__).parents[2] / "docs"
    section = _authored_docs()
    assert "`twin.imagine_look`" in section and "`unavailable`" in section
    assert "playwright install chromium" in section
    assert "`twin.imagine_look`" in (root / "capability-matrix.md").read_text(encoding="utf-8")
    spec = (root / "mcp_spec.md").read_text(encoding="utf-8")
    assert "`twin.imagine_look` (read-only, never held for approval)" in spec
    assert "`revision`" in spec.split("`twin.imagine_look`")[1][:900]


def _scenario_block() -> str:
    start = JS.index("function scenList()")
    end = JS.index("/* ---------- Context edits", start)
    return JS[start:end]


def test_scenarios_the_agent_saved_at_the_top_level_are_listed() -> None:
    # The agent saves its inferred scenarios at config.scenarios; the checklist waits on a
    # person to confirm them. A page that lists only context scenarios shows "No scenario
    # tests yet" and the person has nothing to confirm.
    assert "CFG.scenarios" in _scenario_block().split("function scenEval")[0]


def test_an_inferred_scenario_has_a_confirm_control_that_makes_it_the_persons() -> None:
    block = _scenario_block()
    assert "data-confirm-scn" in block, "an inferred scenario needs a Confirm button"
    # The confirmation is the person's own scenario write, so the checklist reads it as theirs.
    handler = block[block.index("data-confirm-scn", block.index("addEventListener")) :]
    assert "by:'user'" in handler and "dbWrite('scenarios'" in handler


def test_a_user_scenario_shows_no_confirm_control() -> None:
    assert "x.by!=='user'" in _scenario_block()


def _host_screening() -> str:
    start = JS.index("function hostScreening(")
    return JS[start : JS.index("function hostApply(", start)]


def test_an_authored_form_writes_a_screening_entry_from_the_checks_that_apply() -> None:
    # The enclosure report is null for an authored form, so the page must build the
    # screening rules from what the form measured, or no authored studio can ever hand over.
    body = _host_screening()
    assert "FGAuthored.screeningRules" in body
    assert "FG_AUTHORED" in body


def test_the_screening_entry_is_rewritten_when_the_authored_form_is_measured() -> None:
    # The parts draw in a worker, so the measurement arrives after recompute has run.
    assert "'fg-authored'" in JS and "hostScreening" in JS.split("'fg-authored'", 1)[1][:300]


def test_the_page_records_that_a_person_has_seen_the_form_for_this_revision() -> None:
    # The page reports only that the person looked. The server stamps the revision, and the
    # page reads its own revision from the frame address to know whether the record covers it.
    assert "'formSeen'" in JS.split("FG_COLLECTIONS=", 1)[1][:200]
    assert "dbWrite('formSeen'" in JS
    assert "onSeen" in JS, "the mount must be given the hook"
    assert "get('rev')" in JS, "the page must read its revision from its address"
