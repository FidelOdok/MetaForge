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


# -- the look tool the skill sends the agent to ---------------------------------------------


def test_the_skill_sends_the_agent_to_the_look_tool_and_names_its_arguments() -> None:
    from api_gateway.imagine.service import ImagineService
    from tool_registry.tools.twin.adapter import TwinServer
    from twin_core.api import InMemoryTwinAPI

    text = _skill()
    section = _authoring_section()
    assert "`twin.imagine_look`" in text.split("## Rules")[0], "listed in the tool table"
    assert "`twin.imagine_look`" in section
    twin = InMemoryTwinAPI.create()
    manifest = (
        TwinServer(twin=twin, imagine_studio=ImagineService(twin))
        ._tools["twin.imagine_look"]
        .manifest
    )
    for argument in ("revision", "set"):
        assert argument in manifest.input_schema["properties"]
        assert f"`{argument}`" in section, f"the skill does not name the {argument} argument"
    for answer in ("unavailable", "not_authored"):
        assert f"`{answer}`" in section, answer
    assert "could not look" in section


def test_the_imagine_command_tells_the_agent_to_look_after_each_write_of_an_authored_form() -> None:
    from mcp_core.workflows import prompt_body

    command = prompt_body("imagine")
    assert "`twin.imagine_look`" in command
    assert "authored" in command and "could not look" in command


def test_the_skill_lists_every_colour_the_checker_accepts() -> None:
    import json

    catalogue = REPO / "api_gateway" / "imagine" / "formgraph" / "data" / "catalogue.json"
    colours = json.loads(catalogue.read_text(encoding="utf-8"))["colors"]
    text = PLAYBOOK.read_text(encoding="utf-8")
    missing = [c for c in colours if f"`{c}`" not in text]
    assert not missing, f"the skill does not name these colours: {missing}"


def test_the_skill_lists_every_sector_the_checker_accepts() -> None:
    import re

    source = (REPO / "api_gateway" / "imagine" / "formgraph" / "build_page.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"pg\[.sector.\] not in \(([^)]*)\)", source)
    assert match, "the sector check moved: update this test to follow it"
    sectors = re.findall(r"'([a-z]+)'", match.group(1))
    assert len(sectors) == 7, sectors
    text = PLAYBOOK.read_text(encoding="utf-8")
    missing = [x for x in sectors if f"`{x}`" not in text]
    assert not missing, f"the skill does not name these sectors: {missing}"


def test_the_skill_lists_every_fixed_list_word_the_checker_holds_a_field_to() -> None:
    import json
    import re

    root = REPO / "api_gateway" / "imagine" / "formgraph"
    source = (root / "build_page.py").read_text(encoding="utf-8")
    catalogue = json.loads((root / "data" / "catalogue.json").read_text(encoding="utf-8"))
    programme = json.loads((root / "data" / "programme.json").read_text(encoding="utf-8"))

    def words(pattern: str) -> list[str]:
        match = re.search(pattern, source)
        assert match, f"the check moved, update this test: {pattern}"
        return re.findall(r"'([A-Za-z_\-]+)'", match.group(1))

    expected: dict[str, list[str]] = {
        "environment.sun": words(r"env\.get\('sun', 'none'\) not in \(([^)]*)\)"),
        "environment.vibration": words(r"env\.get\('vibration', 'fixed'\) not in \(([^)]*)\)"),
        "programme.markets": words(r"str\(m\)\.upper\(\) not in \(([^)]*)\)"),
        "programme.segment": words(r"pg\['segment'\] not in \(([^)]*)\)"),
        "view.tryit": words(r"v\.get\('tryit', 'device'\) not in \(([^)]*)\)"),
        "programme.phase": [p["id"] for p in programme["phases"]],
        "simulations": list(catalogue["simulations"]),
        "level": list(catalogue["levels"]),
    }
    status = re.search(r"LEDGER_STATUS = \{([^}]*)\}", source)
    assert status
    expected["programme.ledger[].status"] = re.findall(r"'([A-Za-z\-]+)'", status.group(1))
    for key, options in catalogue["decisions"].items():
        expected[f"decisions.{key}"] = list(options)
    for req, spec in catalogue["requirements"].items():
        for value in spec.values():
            if isinstance(value, list):
                expected[f"requirements.{req}"] = list(value)
    environment_keys = re.search(r"known = \{([^}]*)\}", source)
    assert environment_keys
    expected["environment keys"] = re.findall(r"'([A-Za-z_]+)'", environment_keys.group(1))

    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "### Words the checker holds to a list" in text
    table = text.split("### Words the checker holds to a list", 1)[1].split("This skeleton", 1)[0]
    missing = {
        field: [w for w in options if f"`{w}`" not in table]
        for field, options in expected.items()
        if any(f"`{w}`" not in table for w in options)
    }
    assert all(len(v) > 0 for v in expected.values()), expected
    assert not missing, f"the allowed-values table is missing: {missing}"


def test_the_skill_tells_the_agent_to_keep_contact_details_out_of_the_project() -> None:
    text = PLAYBOOK.read_text(encoding="utf-8")
    rules = " ".join(text.split("## Rules", 1)[1].split("## The config", 1)[0].split())
    assert "Keep the person's details out of the project" in rules
    for word in ("company name", "email address", "project description", "studio"):
        assert word in rules, f"the rule does not mention: {word}"


def test_the_skill_tells_the_agent_to_resolve_each_parts_3d_body_and_to_report_the_gaps() -> None:
    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    for phrase in (
        "twin.imagine_resolve_part",
        "`package_text`",
        "Never type a size you did not read",
        "list every part that is not `exact`",
        "never call an approximation or a placeholder the real part",
    ):
        assert phrase in text, phrase
    from api_gateway.imagine.formgraph import fg_context

    for word in (*fg_context.FIDELITY, "read", "typical", "generic"):
        assert f"`{word}`" in text, word


def test_the_skills_route_for_a_function_outside_the_catalogue_passes_the_checker() -> None:
    import copy

    from api_gateway.imagine import engine_page as ep
    from api_gateway.imagine.formgraph import build_page
    from tests.unit.imagine_fixtures import _part, full_config

    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    for phrase in ("catalogue.blocks.e_<name>", 'role: "fn:e_<name>"', "parts.chosen"):
        assert phrase in text, phrase

    def check(*, block: bool, part: bool) -> list[str]:
        cfg = copy.deepcopy(full_config())
        cfg["functions"].append("e_press")
        if block:
            cfg.setdefault("catalogue", {}).setdefault("blocks", {})["e_press"] = {
                "name": "Pressure sensor"
            }
        if part:
            cfg["context"]["components"]["parts"].append(
                _part("press-a", "fn:e_press", "Pressure sensor", [3.0, 3.0, 1.0], "i2c")
            )
        ctx = ep._inline_context(cfg.pop("context"))
        errors, _ = build_page.validate(cfg, ep.catalogue(), ctx)
        return errors

    assert check(block=True, part=True) == []
    assert check(block=False, part=False), "an unknown function id must still be refused"
    assert check(block=False, part=True), "a part claiming an unknown function must be refused"


def test_the_skill_tells_the_agent_to_link_a_component_to_its_part_and_leave_the_code_out() -> None:
    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    for phrase in (
        "`component_ref`",
        "context.components.parts",
        "Leave `code` out",
        "writes it again from the part each time",
        "one with no link is a stand in",
    ):
        assert phrase in text, phrase


def test_the_skill_says_every_form_is_authored_and_no_longer_offers_the_enclosure() -> None:
    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    assert "is `authored`, always: you build the form from parts" in text
    assert "The old `enclosure` template was removed and is refused" in text
    for stale in ("Use `enclosure` when", "The `enclosure` template draws one thing"):
        assert stale not in text, stale
    assert '"template": "enclosure"' not in text


def test_the_skill_names_every_advisory_form_finding_the_service_can_return() -> None:
    rules = pytest.importorskip("metaforge_verification.profile.rules")
    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    names = [r.check_id for r in rules.RULES if r.check_id.startswith("form.")]
    assert len(names) >= 5, names
    missing = [n for n in names if f"`{n}`" not in text]
    assert not missing, f"the skill does not name: {missing}"


def test_the_skill_teaches_the_look_measure_fix_loop_and_its_limits() -> None:
    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    for phrase in (
        "**Read what the look measured.**",
        "`support.tip_angle_deg`",
        "`margin_mm` below zero",
        "`support` is null",
        "`overhang_share`",
        "`overlaps`",
        "write, look again and say how the angle moved",
        "Never call these measurements verified",
        "wall thickness",
    ):
        assert phrase in text, phrase


def test_the_skills_tip_threshold_is_the_one_the_service_uses() -> None:
    rules = pytest.importorskip("metaforge_verification.profile.rules")
    text = " ".join(PLAYBOOK.read_text(encoding="utf-8").split())
    assert f"under {rules.TIP_SCREEN_DEG:.0f} degrees" in text


def test_the_imagine_command_tells_the_agent_to_read_the_measurements() -> None:
    from mcp_core.workflows import WORKFLOWS

    text = " ".join(str(w) for w in WORKFLOWS.values() if "imagine" in str(w).lower())
    assert "measured" in text and "tip" in text
