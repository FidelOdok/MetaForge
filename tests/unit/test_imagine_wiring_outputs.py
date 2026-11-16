"""What the handover makes from a studio's connection list (FORGE-887).

Live evidence (night-light studio, 2026-11-16): the module build's wiring lived only as words
in an architecture decision, so firmware had no pin map, the BOM had no wire and nobody had a
wiring order.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine import wiring_outputs


def _iface(iid: str, bus: str, dirn: str, **extra: Any) -> dict[str, Any]:
    return {"id": iid, "bus": bus, "dir": dirn, **extra}


def _ctx(conns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "components": {
            "parts": [
                {
                    "id": "board-a",
                    "name": "XIAO ESP32S3",
                    "interfaces": [
                        _iface("v5", "pwr", "out", imax_mA=2000),
                        _iface("gnd", "pwr", "bi"),
                        _iface("d6", "gpio", "out"),
                        _iface("i2c", "i2c", "bi", pins=["SDA", "SCL"]),
                    ],
                },
                {
                    "id": "ring-a",
                    "name": "NeoPixel Ring 12",
                    "interfaces": [
                        _iface("pwr", "pwr", "in", imax_mA=720),
                        _iface("gnd", "pwr", "bi"),
                        _iface("din", "gpio", "in"),
                    ],
                },
                {"id": "lux-a", "interfaces": [_iface("i2c", "i2c", "bi", pins=["SDA", "SCL"])]},
            ]
        },
        "connections": {"schemaVersion": 1, "connections": conns},
    }


CONNS = [
    {"id": "c3", "net": "DATA", "from": "board-a.d6", "to": "ring-a.din", "length_mm": 40},
    {"id": "c1", "net": "5V", "from": "board-a.v5", "to": "ring-a.pwr", "length_mm": 45},
    {"id": "c2", "net": "GND", "from": "board-a.gnd", "to": "ring-a.gnd", "length_mm": 45},
]


def test_the_wiring_table_goes_ground_then_power_then_signals() -> None:
    rows = wiring_outputs.wiring_table(_ctx(CONNS))
    assert [r["id"] for r in rows] == ["c2", "c1", "c3"]
    assert [r["kind"] for r in rows] == ["ground", "power", "signal"]
    # 15 mm for stripping is added to each recorded run
    assert rows[0]["length_mm"] == 60


def test_gauges_follow_the_harness_rule() -> None:
    rows = {r["id"]: r for r in wiring_outputs.wiring_table(_ctx(CONNS))}
    # 0.72 A, 0.9 A with headroom: 28 AWG (1.4 A)
    assert rows["c1"]["awg"] == 28 and "0.72 A" in rows["c1"]["sized_from"]
    assert rows["c3"]["awg"] == 28 and rows["c3"]["sized_from"] == "signal"
    fixed = [{**CONNS[1], "wire": {"awg": 22}}]
    assert wiring_outputs.wiring_table(_ctx(fixed))[0]["awg"] == 22


def test_the_harness_bom_sums_the_cut_list_and_never_guesses_a_length() -> None:
    lines = wiring_outputs.harness_lines(_ctx(CONNS))
    # power and data are 28 AWG; ground records no current, so it takes the 26 AWG power default
    assert [(x["item"], x["cuts"], x["qty_m"]) for x in lines] == [
        ("Hook-up wire, 28 AWG", 2, 0.115),  # (45 + 40 + 2 x 15) mm
        ("Hook-up wire, 26 AWG", 1, 0.06),
    ]
    unmeasured = [{k: v for k, v in c.items() if k != "length_mm"} for c in CONNS]
    line = wiring_outputs.harness_lines(_ctx(unmeasured))[0]
    assert line["qty_m"] is None and "2 of 2 lengths still to measure" in line["spec"]


def test_the_pin_map_names_each_wired_end_its_net_and_what_it_joins() -> None:
    conns = [*CONNS, {"id": "c4", "net": "SDA", "from": "board-a.i2c.SDA", "to": "lux-a.i2c.SDA"}]
    specs = {s["name"]: s for s in wiring_outputs.pin_parameter_specs(_ctx(conns))}
    assert specs["pin_board_a_d6"]["value"] == "DATA"
    assert "ring-a.din" in specs["pin_board_a_d6"]["note"]
    assert specs["pin_board_a_i2c_sda"]["label"] == "XIAO ESP32S3: i2c.SDA"
    assert len(specs) == 8  # every end of the four wires


def test_the_sequence_ends_with_the_checks_before_power_up() -> None:
    steps = wiring_outputs.wiring_sequence(_ctx(CONNS))
    assert steps[0].startswith("1. Wire GND (ground)") and "cut 60 mm" in steps[0]
    assert "short between any power net and ground" in steps[-1]


def test_no_connection_list_makes_nothing() -> None:
    ctx = _ctx([])
    assert wiring_outputs.handover_record(ctx, "lamp") is None
    assert wiring_outputs.pin_parameter_specs(ctx) == []
    assert wiring_outputs.harness_lines(ctx) == []


async def test_the_handover_writes_the_wiring_and_the_pin_map() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI

    ops.init_operations(None)
    try:
        svc = ImagineService(InMemoryTwinAPI.create())
        cfg = full_config()
        cfg["context"]["connections"] = {
            "schemaVersion": 1,
            "connections": [
                {"id": "c1", "net": "SDA", "from": "th-a.io", "to": "th-b.io", "length_mm": 30}
            ],
        }
        pid = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12"
        await svc.put_studio(pid, cfg, actor="test")
        head = await svc._head(pid)
        assert head is not None
        decisions: list[dict[str, Any]] = []
        real = svc.bridge.record_decision

        async def spy(project_id: str, **kw: Any) -> Any:
            decisions.append(kw)
            return await real(project_id, **kw)

        svc.bridge.record_decision = spy  # type: ignore[method-assign]
        contracts: list[Any] = []
        svc.bridge.write_contract = (  # type: ignore[method-assign]
            lambda project_id, contract, actor: _keep(contracts, contract)
        )
        created = await svc._seed(pid, head, "test")
        wiring = next(d for d in decisions if d["title"].startswith("Imagine wiring"))
        assert "| 1 | SDA | th-a.io | th-b.io | 28 AWG | 45 |" in wiring["rationale"]
        assert head.state["wiring"]["decision"] in created
        fw = next(c for c in contracts if c.kind.value.endswith("firmware"))
        names = [p.name for p in fw.parameters]
        assert names[:2] == ["pin_th_a_io", "pin_th_b_io"]
    finally:
        ops.init_operations(None)


async def _keep(store: list[Any], contract: Any) -> str:
    store.append(contract)
    return f"contract:{len(store)}"
