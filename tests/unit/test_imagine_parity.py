"""Parity: the verification service must decide what the handover checklist decides.

The checklist is what MetaForge shipped; the `imagine-v1` profile in
MetaForge-Verification now owns that decision. Moving a decision must not change
an outcome, so this feeds the same studio state to both and compares every
item. It needs the Verification package, which MetaForge does not depend on:

    PYTHONPATH=<MetaForge-Verification>/src pytest tests/unit/test_imagine_parity.py

Without the package it SKIPS, loudly. A skip is not a pass. Set
``METAFORGE_REQUIRE_VERIFICATION_PARITY=1`` (as CI should, once it installs the
package) to turn a missing package into a failure.
"""

from __future__ import annotations

import copy
import os
import random
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api_gateway.imagine.concept_revision import build_concept_revision
from api_gateway.imagine.service import ImagineService, _Head
from tests.unit.imagine_fixtures import full_config
from twin_core.api import InMemoryTwinAPI

REQUIRE = "METAFORGE_REQUIRE_VERIFICATION_PARITY"

State = dict[str, Any]
Mutator = Callable[[State], None]


@pytest.fixture(scope="module")
def verification() -> Any:
    try:
        from metaforge_verification.profile import evaluate
        from metaforge_verification.profile.contracts import ValidationRequest
    except ImportError:
        if os.environ.get(REQUIRE):
            pytest.fail(f"{REQUIRE} is set but metaforge_verification is not importable")
        pytest.skip(
            "metaforge_verification is not installed: parity NOT checked "
            "(set PYTHONPATH to MetaForge-Verification/src)"
        )

    def run(revision: dict[str, Any]) -> Any:
        return evaluate.validate(ValidationRequest.model_validate({"revision": revision}))

    return run


# -- the same state, built once, fed to both ---------------------------------


def ready_state() -> State:
    """A studio the checklist reads as fully done, except nothing it cannot be."""
    cfg = copy.deepcopy(full_config())
    # Explicit verification methods, so requirements read done and not assumed.
    for r in cfg["requirements"]:
        r["verify"] = "test"
    cols: dict[str, dict[str, Any]] = {
        k: {}
        for k in (
            "choices",
            "scenarios",
            "contextEdits",
            "assumptions",
            "requirements",
            "ledger",
            "screening",
        )
    }
    for a in cfg["programme"]["assumptions"]:
        cols["assumptions"][a["id"]] = {"confirmed": True}
    cols["screening"]["latest"] = {"rule_version": "formgraph@2"}
    return {"config": cfg, "collections": cols, "mesh": True}


def part(s: State, pid: str) -> dict[str, Any]:
    return next(p for p in s["config"]["context"]["components"]["parts"] if p["id"] == pid)


def set_part(s: State, pid: str, **fields: Any) -> None:
    for k, v in fields.items():
        if v is None:
            part(s, pid).pop(k, None)
        else:
            part(s, pid)[k] = v


def chosen_ids(s: State) -> list[str]:
    return ["usbc-a", "th-a", "lte-a", "btn-a", "led-a"]


def no_interfaces_anywhere(s: State) -> None:
    for pid in chosen_ids(s):
        part(s, pid)["interfaces"] = []
    s["config"]["programme"].pop("interfaces", None)


def add_board(s: State, **over: Any) -> None:
    board = copy.deepcopy(part(s, "usbc-a"))
    board.update({"id": "board-x", "role": "board", **over})
    s["config"]["context"]["components"]["parts"].append(board)


def drop_decision(s: State) -> None:
    opts = s["config"]["context"]["options"]
    opts["decisions"] = [d for d in opts["decisions"] if d["id"] != "th"]


def process(s: State, **fields: Any) -> None:
    for k, v in fields.items():
        if v is None:
            s["config"]["process"].pop(k, None)
        else:
            s["config"]["process"][k] = v


def authored_form(s: State) -> None:
    from tests.unit.imagine_fixtures import authored_lamp_form

    s["config"]["form"] = authored_lamp_form()


def authored_form_of_bought_parts(s: State) -> None:
    from tests.unit.imagine_fixtures import authored_lamp_form

    form = authored_lamp_form()
    form["authored"]["parts"] = [p for p in form["authored"]["parts"] if p["kind"] == "component"]
    s["config"]["form"] = form


MUTATORS: dict[str, Mutator] = {
    # the form (spec 23.6): never one of the nine items, so no outcome may move
    "form:authored": authored_form,
    "form:authored-only-bought": authored_form_of_bought_parts,
    # ledger
    "ledger:none": lambda s: s["config"]["programme"].update(ledger=[]),
    "ledger:unreviewed": lambda s: s["config"]["programme"]["ledger"][0].update(
        status="unreviewed"
    ),
    "ledger:unrepresentable": lambda s: s["config"]["programme"]["ledger"][1].update(
        status="unrepresentable"
    ),
    "ledger:status-unset": lambda s: s["config"]["programme"]["ledger"][2].pop("status"),
    "ledger:collection-reopens": lambda s: s["collections"]["ledger"].update(
        l0={"status": "unreviewed"}
    ),
    "ledger:collection-resolves": lambda s: (
        s["config"]["programme"]["ledger"][0].update(status="unreviewed"),
        s["collections"]["ledger"].update(l0={"status": "mapped"}),
    ),
    "ledger:non-dict-row": lambda s: s["config"]["programme"]["ledger"].__setitem__(3, "text"),
    # assumptions
    "asm:one-unconfirmed": lambda s: s["collections"]["assumptions"].update(
        cover={"confirmed": False}
    ),
    "asm:accepted": lambda s: s["collections"]["assumptions"].update(
        cover={"confirmed": False, "accepted": True}
    ),
    "asm:accepted-by-agent": lambda s: s["collections"]["assumptions"].update(
        cover={"accepted": True, "agent": "claude", "on_behalf_of": "fidel"}
    ),
    "asm:none-recorded": lambda s: s["config"]["programme"].update(assumptions=[]),
    "asm:never-seen": lambda s: s["collections"]["assumptions"].clear(),
    # scenarios
    "scen:none": lambda s: s["config"].update(scenarios=[]),
    "scen:inferred": lambda s: s["config"]["scenarios"][2].update(by="agent"),
    "scen:inferred-confirmed": lambda s: (
        s["config"]["scenarios"][2].update(by="agent"),
        s["collections"]["scenarios"].update(knock={"confirmed": True}),
    ),
    # requirements
    "req:default-verify": lambda s: s["config"]["requirements"][0].pop("verify"),
    "req:no-source": lambda s: s["config"]["requirements"][0].update(source=""),
    "req:whitespace-source": lambda s: s["config"]["requirements"][0].update(source="   "),
    # The fixture also states custom requirements, so both go to leave none.
    "req:none": lambda s: (
        s["config"].update(requirements=[]),
        s["config"]["programme"].update(customRequirements=[]),
    ),
    "req:custom-no-source": lambda s: s["config"]["programme"]["customRequirements"][0].update(
        source=""
    ),
    # parts
    "parts:no-functions": lambda s: s["config"].update(functions=[]),
    "parts:function-without-part": lambda s: s["config"]["context"]["components"].update(
        parts=[p for p in s["config"]["context"]["components"]["parts"] if p["role"] != "fn:e_led"]
    ),
    "parts:no-source": lambda s: (
        set_part(s, "usbc-a", datasheet=None),
        part(s, "usbc-a")["geometry"].pop("source"),
    ),
    "parts:url-source": lambda s: (
        set_part(s, "usbc-a", datasheet=None, url="https://u.example/x"),
        part(s, "usbc-a")["geometry"].pop("source"),
    ),
    "parts:cost-source": lambda s: (
        set_part(s, "usbc-a", datasheet=None, cost={"source": "quote"}),
        part(s, "usbc-a")["geometry"].pop("source"),
    ),
    "parts:geometry-source-only": lambda s: set_part(s, "usbc-a", datasheet=None),
    "parts:no-date": lambda s: set_part(s, "usbc-a", retrieved=None),
    "parts:date-key": lambda s: set_part(s, "usbc-a", retrieved=None, date="2026-11-06"),
    "parts:no-interfaces": lambda s: set_part(s, "usbc-a", interfaces=[]),
    "parts:interfaces-dict": lambda s: set_part(s, "usbc-a", interfaces={"io": 1}),
    "parts:no-envelope": lambda s: part(s, "usbc-a")["geometry"].pop("envelope"),
    "parts:other-candidate-picked": lambda s: s["collections"]["choices"].update(
        th={"pick": "th-b"}
    ),
    "parts:two-candidates-no-decision": drop_decision,
    "parts:board-complete": lambda s: add_board(s),
    "parts:board-incomplete": lambda s: add_board(s, interfaces=[]),
    "parts:two-defects": lambda s: (
        part(s, "led-a")["geometry"].pop("envelope"),
        set_part(s, "th-a", retrieved=None),
    ),
    # manufacturing context
    "proc:no-material": lambda s: process(s, material=None),
    "proc:no-qty": lambda s: process(s, qty=None),
    "proc:qty-zero": lambda s: process(s, qty=0),
    "proc:qty-string": lambda s: process(s, qty="250"),
    "proc:qty-float": lambda s: process(s, qty=100.5),
    "proc:no-process": lambda s: process(s, id=None),
    # interfaces
    "iface:none-anywhere": no_interfaces_anywhere,
    "iface:programme-only": lambda s: (
        no_interfaces_anywhere(s),
        s["config"]["programme"].update(interfaces=[{"id": "x"}]),
    ),
    # screening and the mesh
    "screening:none": lambda s: s["collections"]["screening"].clear(),
    "mesh:none": lambda s: s.update(mesh=False),
}


# -- reading both sides as the same vocabulary -----------------------------------


async def checklist_of(state: State) -> tuple[dict[str, str], bool]:
    service = ImagineService(InMemoryTwinAPI.create())
    service.bridge.has_concept_mesh = AsyncMock(return_value=state["mesh"])  # type: ignore[method-assign]
    head = _Head(
        revision=1,
        node_id=uuid4(),
        metadata={},
        state={
            "config": state["config"],
            "collections": state["collections"],
            "status": "draft",
            "events": {},
        },
        updated_at=None,
    )
    result = await service._checklist(str(uuid4()), head)
    return {i.id: i.status for i in result.items}, result.ready


def service_items(report: Any) -> dict[str, str]:
    """The service's findings, folded back into the checklist's nine items."""

    def status(check_id: str) -> str:
        found = [f for f in report.findings if f.check_id == check_id]
        assert found, f"no finding for {check_id}"
        if any(f.status == "fail" for f in found):
            return "missing"
        if any(f.status == "assumed" for f in found):
            return "assumed"
        return "done"

    parts_failed = any(
        f.status == "fail"
        for f in report.findings
        if f.check_id in ("parts.chosen", "parts.source", "parts.interfaces", "parts.envelope")
    )
    mesh_failed = status("concept.mesh") == "missing"
    chosen_failed = any(f.status == "fail" for f in report.findings if f.check_id == "parts.chosen")
    return {
        "ledger": status("ledger.reviewed"),
        "assumptions": status("assumptions.settled"),
        "scenarios": status("scenarios.confirmed"),
        "requirements": status("requirements.sourced"),
        "parts": "missing" if parts_failed else "done",
        "manufacturing_context": status("process.context"),
        # The checklist folds "a chosen part for every function" and the mesh into one item.
        "concept_assembly": "missing" if (chosen_failed or mesh_failed) else "done",
        "interfaces": status("interfaces.described"),
        "screening": status("screening.stored"),
    }


async def compare(state: State, verification: Any, name: str) -> None:
    items, is_ready = await checklist_of(state)
    revision = build_concept_revision(
        str(uuid4()),
        1,
        state["config"],
        state["collections"],
        has_mesh=state["mesh"],
    )
    report = verification(revision)
    got = service_items(report)
    assert got == items, f"{name}: checklist {items} != service {got}"
    assert is_ready == (report.verdict == "ready_for_handover"), (
        f"{name}: checklist ready={is_ready} but the service verdict is {report.verdict}"
    )


# -- the matrix ---------------------------------------------------------------------


async def test_the_baseline_is_ready_in_both(verification: Any) -> None:
    state = ready_state()
    items, is_ready = await checklist_of(state)
    assert is_ready and set(items.values()) == {"done"}, items
    await compare(state, verification, "baseline")


@pytest.mark.parametrize("name", sorted(MUTATORS))
async def test_each_single_degradation_agrees(name: str, verification: Any) -> None:
    state = ready_state()
    MUTATORS[name](state)
    await compare(state, verification, name)


async def test_combinations_agree(verification: Any) -> None:
    """Interplay, not just single causes: 150 seeded combinations of up to four."""
    rng = random.Random(20261109)
    names = sorted(MUTATORS)
    for _ in range(150):
        picked = rng.sample(names, rng.randint(2, 4))
        state = ready_state()
        for n in picked:
            try:
                MUTATORS[n](state)
            except (KeyError, IndexError, AttributeError, StopIteration):
                # An earlier mutation removed what this one edits (for example an
                # emptied list, or a part that no longer exists, which ``next()`` reports
                # as StopIteration). Skip it, as a real edit would not apply either.
                continue
        await compare(state, verification, " + ".join(picked))


# -- the form: sent only when authored, and it never moves the decision --------------


def _revision_of(state: State) -> dict[str, Any]:
    return build_concept_revision(
        str(uuid4()), 1, state["config"], state["collections"], has_mesh=state["mesh"]
    )


async def test_an_authored_form_reaches_the_service_and_is_named_not_assessed(
    verification: Any,
) -> None:
    state = ready_state()
    authored_form(state)
    revision = _revision_of(state)
    assert revision["form"]["kind"] == "authored"
    # The same revision without the form block is what an older sender would send.
    plain = verification({k: v for k, v in revision.items() if k != "form"})
    report = verification(revision)
    note = [f for f in report.findings if f.check_id == "form.enclosure_checks"]
    assert len(note) == 1 and note[0].status == "not_assessed" and note[0].severity == "advisory"
    assert report.verdict == plain.verdict
    assert [(g.gate, g.decision) for g in report.gates] == [
        (g.gate, g.decision) for g in plain.gates
    ]
    assert [f.id for f in report.findings if not f.check_id.startswith("form.")] == [
        f.id for f in plain.findings
    ]


async def test_a_form_of_only_bought_parts_is_called_out_and_still_moves_nothing(
    verification: Any,
) -> None:
    state = ready_state()
    plain = verification(_revision_of(state))
    authored_form_of_bought_parts(state)
    report = verification(_revision_of(state))
    mix = [f for f in report.findings if f.check_id == "form.confidence_mix"]
    assert len(mix) == 1 and mix[0].status == "fail" and mix[0].severity == "advisory"
    assert report.verdict == plain.verdict == "ready_for_handover"


async def test_a_revision_with_no_form_block_still_gets_no_form_finding(verification: Any) -> None:
    """An older sender, or a legacy studio, sends no form: the service adds no form finding."""
    revision = {k: v for k, v in _revision_of(ready_state()).items() if k != "form"}
    assert "form" not in revision
    assert not [f for f in verification(revision).findings if f.check_id.startswith("form.")]


# -- where the two deliberately differ ----------------------------------------------


async def test_a_blank_part_source_is_stricter_in_the_service(verification: Any) -> None:
    """The checklist counts "   " as a source; the service does not.

    Deliberate: whitespace is not a source. It is the one known place the
    service is stricter than the checklist it replaces, so a studio that is
    ready by the old rule can read not ready by the new one, never the reverse.
    """
    state = ready_state()
    set_part(state, "usbc-a", datasheet="   ")
    part(state, "usbc-a")["geometry"].pop("source")
    items, _ = await checklist_of(state)
    revision = build_concept_revision(
        str(uuid4()), 1, state["config"], state["collections"], has_mesh=True
    )
    assert items["parts"] == "done"
    assert service_items(verification(revision))["parts"] == "missing"


async def test_a_blank_part_date_is_stricter_in_the_service(verification: Any) -> None:
    state = ready_state()
    set_part(state, "usbc-a", retrieved="  ")
    items, _ = await checklist_of(state)
    revision = build_concept_revision(
        str(uuid4()), 1, state["config"], state["collections"], has_mesh=True
    )
    assert items["parts"] == "done"
    assert service_items(verification(revision))["parts"] == "missing"
