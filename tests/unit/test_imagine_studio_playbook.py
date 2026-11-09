"""The imagine_studio playbook: its config skeleton passes the real checks (FORGE-824).

A live chat run guessed the formgraph config shape and was refused six times
(unknown top-level keys, a missing product, a bad engine, a non-slug id, an
unknown template, a bad priority). The playbook carries a skeleton so the
agent gets it right first time; this keeps that skeleton honest against the
validator ``twin.imagine_put_studio`` runs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from api_gateway.chat.playbooks import load_playbooks
from api_gateway.imagine.engine_page import validate_config
from api_gateway.imagine.formgraph import authored

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO / "mcp_core" / "plugin_skills" / "imagine_studio" / "SKILL.md"


def _skeleton() -> dict[str, Any]:
    blocks = re.findall(r"```json\n(.*?)\n```", PLAYBOOK.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, "the playbook carries exactly one JSON skeleton"
    data: dict[str, Any] = json.loads(blocks[0])
    return data


def test_the_skeleton_passes_the_formgraph_checks() -> None:
    # validate_config raises ConfigError on any error.
    validated = validate_config(_skeleton())
    assert validated.config["engine"] == "formgraph@2"


def test_the_skeleton_carries_what_the_live_run_left_out() -> None:
    cfg = _skeleton()
    programme = cfg["programme"]
    assert programme["ledger"] and programme["assumptions"] and programme["markets"]
    assert programme["interfaces"]
    assert cfg["environment"]
    assert cfg["scenarios"]
    assert isinstance(cfg["process"]["qty"], int)
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", cfg["product"]["id"])
    assert {r["pri"] for r in cfg["requirements"]} <= {"Must", "Should", "Off"}
    assert cfg["context"]["components"]["parts"]


def test_the_chat_agent_can_read_it_by_name() -> None:
    playbook = load_playbooks(REPO)["imagine_studio"]
    assert "Use when" in playbook.description
    body = playbook.body
    for tool in (
        "twin.imagine_get_studio",
        "twin.imagine_put_studio",
        "twin.imagine_handover_status",
        "twin.design_cad_loop",
        "twin.generate_concept_mesh",
    ):
        assert f"`{tool}`" in body, tool
    assert "## After handover" in body
    # FORGE-824: hand over only under the owner's delegation, else ask.
    assert "Hand over only when delegated" in body
    assert "`twin.imagine_handover`" in body
    assert "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY" in body
    assert "V2" in body


def test_the_skeleton_is_a_desk_lamp_printed_in_fdm_with_an_led_driver() -> None:
    """FORGE-827: no process stands in for another, and the lamp has its driver."""
    cfg = _skeleton()
    assert cfg["process"]["id"] == "fdm" and cfg["process"]["material"] == "petg"
    assert "e_ldrv" in cfg["functions"]
    assert any(p["role"] == "fn:e_ldrv" for p in cfg["context"]["components"]["parts"])


def test_a_delegated_run_carries_on_without_a_person() -> None:
    """FORGE-827: accept, concept mesh, hand over, then Develop, Check and Make."""
    body = load_playbooks(REPO)["imagine_studio"].body
    for tool in (
        "twin.imagine_accept",
        "twin.render_concept_image",
        "twin.generate_concept_mesh",
        "twin.job_status",
        "twin.imagine_handover",
        "twin.record_decision",
        "twin.evaluate_dfm",
        "twin.get_release_checklist",
    ):
        assert f"`{tool}`" in body, tool
    assert "A delegated run carries on" in body
    assert "X-MetaForge-On-Behalf-Of" in body
    from mcp_core.workflows import WORKFLOWS

    command = WORKFLOWS["imagine"][1]
    for tool in ("twin.imagine_accept", "twin.generate_concept_mesh", "twin.record_decision"):
        assert f"`{tool}`" in command, tool
    assert "Develop, Check and Make" in command


def test_the_playbook_has_no_em_dashes() -> None:
    assert "—" not in PLAYBOOK.read_text(encoding="utf-8")


# -- authoring a form (spec 23.6) --------------------------------------------------------

RUNTIME_CHECK = REPO / "tests" / "unit" / "imagine_authored_runtime_check.mjs"


def _skill() -> str:
    return PLAYBOOK.read_text(encoding="utf-8")


def _authoring_section() -> str:
    text = _skill()
    start = text.index("## Authoring a form")
    end = text.index("## Steps")
    return text[start:end]


def _authored_example() -> dict[str, Any]:
    blocks = re.findall(r"```json authored-form\n(.*?)\n```", _skill(), re.S)
    assert len(blocks) == 1, "the skill carries exactly one authored-form example"
    data: dict[str, Any] = json.loads(blocks[0])
    return data


def _node(*args: str) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the authored-form runtime cannot be exercised")
    proc = subprocess.run(
        [node, str(RUNTIME_CHECK), *args], capture_output=True, text=True, timeout=120, cwd=REPO
    )
    assert proc.returncode == 0, proc.stderr
    out: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


def test_the_skill_names_both_form_templates_and_no_longer_says_enclosure_is_the_only_one() -> None:
    text = _skill()
    assert "Today that is only" not in text
    assert "`authored`" in text and "`enclosure`" in text
    assert "## Authoring a form" in text


def test_the_authored_example_passes_the_checks_the_write_runs() -> None:
    cfg = _skeleton()
    cfg["form"] = _authored_example()
    validated = validate_config(cfg)
    assert validated.config["form"]["template"] == "authored"


def test_the_authored_example_really_draws_in_the_sandbox() -> None:
    form = _authored_example()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"params": form["params"], "parts": form["authored"]["parts"]}, handle)
    result = _node("--form", handle.name)
    assert all(p["error"] is None for p in result["parts"]), result["parts"]
    assert {p["kind"] for p in result["parts"]} >= {"generated", "component"}
    assert all(p["triangles"] > 0 for p in result["parts"])
    assert result["size"][2] > 100, "a desk lamp is taller than a hand"


def test_the_skill_lists_every_word_the_checker_refuses_and_every_budget() -> None:
    section = _authoring_section()
    for token in authored.BANNED_TOKENS:
        assert f"`{token}`" in section, f"{token} is refused by the checker but not in the skill"
    for number in (
        authored.MAX_PARTS,
        authored.MAX_PART_CODE_BYTES,
        authored.MAX_TOTAL_CODE_BYTES,
        authored.MAX_PART_TRIANGLES,
        authored.MAX_TOTAL_TRIANGLES,
    ):
        assert str(number) in section.replace(",", "").replace(" ", ""), number
    for kind in authored.KINDS:
        assert f"`{kind}`" in section, kind


def test_the_skill_documents_exactly_the_shapes_the_sandbox_offers() -> None:
    offered = set(_node("--methods")["methods"])
    documented = set(re.findall(r"`g\.([a-z]+)\(", _authoring_section()))
    assert documented == offered, documented ^ offered


def test_the_skill_tells_the_agent_what_a_form_is_not_assessed_for_and_not_to_claim_it() -> None:
    section = _authoring_section()
    assert "not assessed" in section
    assert "form.enclosure_checks" in section and "form.confidence_mix" in section
    assert "could not look" in section, "an agent that cannot see the form must say so"
