"""Wiring checks on a studio's connection list (FORGE-886).

Live evidence (night-light studio, 2026-11-16): the module build was wired only in words, so
nothing could say a power pin was left off, an input floated or a wire was too thin.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine import wiring_checks


def _iface(iid: str, bus: str, dirn: str, **extra: Any) -> dict[str, Any]:
    return {"id": iid, "bus": bus, "dir": dirn, **extra}


def _ctx(conns: list[dict[str, Any]], unused: list[str] | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"schemaVersion": 1, "connections": conns}
    if unused is not None:
        block["unused"] = unused
    return {
        "components": {
            "parts": [
                {
                    "id": "board-a",
                    "interfaces": [
                        _iface("v5", "pwr", "out", imax_mA=2000),
                        _iface("gnd", "pwr", "bi"),
                        _iface("d6", "gpio", "out"),
                        _iface("d7", "gpio", "bi"),
                    ],
                },
                {
                    "id": "ring-a",
                    "interfaces": [
                        _iface("pwr", "pwr", "in", imax_mA=720),
                        _iface("gnd", "pwr", "bi"),
                        _iface("din", "gpio", "in"),
                        _iface("dout", "gpio", "out"),
                    ],
                },
                {"id": "spare", "excluded": True, "interfaces": [_iface("vin", "pwr", "in")]},
            ]
        },
        "connections": block,
    }


FULL = [
    {"id": "c1", "net": "5V", "from": "board-a.v5", "to": "ring-a.pwr"},
    {"id": "c2", "net": "GND", "from": "board-a.gnd", "to": "ring-a.gnd"},
    {"id": "c3", "net": "DATA", "from": "board-a.d6", "to": "ring-a.din"},
]


def test_a_fully_wired_build_has_no_findings() -> None:
    # outputs (ring dout) and bidirectional spares (board d7) may stay unwired
    assert wiring_checks.wiring_findings(_ctx(FULL)) == []


def test_an_unwired_power_input_and_ground_are_named() -> None:
    found = wiring_checks.wiring_findings(_ctx([FULL[2]]))
    joined = " ".join(found)
    assert '"ring-a.pwr" is a power input' in joined
    assert '"ring-a.gnd"' in joined and '"board-a.gnd"' in joined
    # an excluded part is not in the build
    assert "spare" not in joined


def test_a_floating_input_is_named_and_unused_on_purpose_is_not() -> None:
    conns = FULL[:2]
    assert any(
        "ring-a.din" in f and "floats" in f for f in wiring_checks.wiring_findings(_ctx(conns))
    )
    assert wiring_checks.wiring_findings(_ctx(conns, unused=["ring-a.din"])) == []


def test_a_wire_too_thin_for_its_current_is_named_with_the_gauge_that_fits() -> None:
    # 0.72 A is the lower of the two peaks: 0.9 A with headroom, so 28 AWG (1.4 A) is enough...
    ok = [{**FULL[0], "wire": {"awg": 28}}, *FULL[1:]]
    assert wiring_checks.wiring_findings(_ctx(ok)) == []
    # ...but a 2 A load is 2.5 A with headroom: 26 AWG (2.2 A) is too thin, 24 AWG fits
    ctx = _ctx([{**FULL[0], "wire": {"awg": 26}}, *FULL[1:]])
    ctx["components"]["parts"][1]["interfaces"][0]["imax_mA"] = 2000
    found = wiring_checks.wiring_findings(ctx)
    assert len(found) == 1 and "26 AWG" in found[0] and "use 24 AWG" in found[0]


def test_an_unknown_gauge_is_named() -> None:
    found = wiring_checks.wiring_findings(_ctx([{**FULL[0], "wire": {"awg": 30}}, *FULL[1:]]))
    assert len(found) == 1 and "30" in found[0]


def test_no_connection_list_has_no_findings_and_the_item_says_so() -> None:
    ctx = _ctx([])
    assert wiring_checks.wiring_findings(ctx) == []
    status, detail = wiring_checks.checklist_item(ctx)
    assert status == "assumed" and "No connection list" in detail


def test_the_checklist_item_holds_the_handover_on_a_finding() -> None:
    assert wiring_checks.checklist_item(_ctx(FULL))[0] == "done"
    status, detail = wiring_checks.checklist_item(_ctx([FULL[2]]))
    assert status == "missing" and "ring-a.pwr" in detail


def test_a_part_alone_has_nothing_to_wire() -> None:
    ctx = {"components": {"parts": [{"id": "a", "interfaces": [_iface("v", "pwr", "in")]}]}}
    assert wiring_checks.checklist_item(ctx)[0] == "done"


async def test_a_studio_write_reports_the_wiring_findings() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI

    ops.init_operations(None)
    try:
        cfg = full_config()
        cfg["context"]["connections"] = {
            "schemaVersion": 1,
            "connections": [
                {"id": "c1", "net": "SDA", "from": "th-a.io", "to": "th-b.io", "wire": {"awg": 30}}
            ],
        }
        svc = ImagineService(InMemoryTwinAPI.create())
        pid = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12"
        out = await svc.put_studio(pid, cfg, actor="test")
        wiring = [w for w in out.warnings if w.startswith("Wiring:")]
        check = await svc.checklist(pid)
        item = next(i for i in check.items if i.id == "wiring")
        assert len(wiring) == 1 and '"c1"' in wiring[0]
        assert item.status == "missing" and "c1" in item.detail
    finally:
        ops.init_operations(None)
