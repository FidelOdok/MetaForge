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
from pathlib import Path
from typing import Any

from api_gateway.chat.playbooks import load_playbooks
from api_gateway.imagine.engine_page import validate_config

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
    assert "Never hand over" in body
    assert "V2" in body


def test_the_playbook_has_no_em_dashes() -> None:
    assert "—" not in PLAYBOOK.read_text(encoding="utf-8")
