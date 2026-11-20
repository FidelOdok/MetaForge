"""The system schematic: boards, bought modules and the cables between them.

Not every component sits on the same board. A desk lamp has a controller board
in the base, an LED board in the head and a touch board under the cover, joined
by cables, with a bought mains module seen only by its interfaces. The system
architecture records that; each board then has its own schematic and layout.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.twin.structured_document_recorder import render_system_architecture_markdown


def _lamp() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = [
        {"name": "Mains module", "kind": "module", "location": "base", "discipline": "electrical"},
        {
            "name": "Controller board",
            "kind": "board",
            "location": "base",
            "parts": [
                {"ref": "U1", "name": "ESP32-C3-WROOM-02"},
                {"ref": "U2", "mpn": "AL8862"},
                {"ref": "U3", "mpn": "AL8862"},
                {"ref": "J4", "name": "LED cable"},
            ],
        },
        {"name": "LED board", "kind": "board", "location": "head", "parts": [{"ref": "D1"}]},
        {"name": "Touch board", "kind": "board", "location": "cover", "parts": [{"ref": "U5"}]},
    ]
    interfaces = [
        {"from": "Mains module", "to": "Controller board", "kind": "cable", "wires": 2,
         "length_mm": 120, "signals": ["5V", "GND"]},
        {"from": "Controller board", "to": "LED board", "kind": "cable", "from_connector": "J4",
         "to_connector": "J1", "wires": 4, "length_mm": 450,
         "signals": ["LED_W+", "LED_W-", "LED_C+", "LED_C-"]},
        {"from": "Controller board", "to": "Touch board", "kind": "cable", "wires": 3,
         "length_mm": 60},
    ]  # fmt: skip
    return components, interfaces


def test_a_lamp_with_three_boards_a_module_and_three_cables() -> None:
    components, interfaces = _lamp()
    md, meta = render_system_architecture_markdown("Lamp", "Lamp", components, interfaces)
    assert meta["boards"] == ["Controller board", "LED board", "Touch board"]
    assert meta["cables"] == 3 and meta["dangling_interfaces"] == []
    assert "## Boards and modules" in md and "## Cables" in md
    assert "Controller board J4" in md and "LED board J1" in md and "450" in md
    assert "U2 AL8862" in md


def test_each_board_has_its_own_reference_designators() -> None:
    components, interfaces = _lamp()
    components[2]["parts"].append({"ref": "J1", "name": "From controller"})
    components[3]["parts"].append({"ref": "J1", "name": "To controller"})
    _, meta = render_system_architecture_markdown("Lamp", "Lamp", components, interfaces)
    assert meta["boards"] == ["Controller board", "LED board", "Touch board"]


def test_a_cable_by_its_older_interface_type_counts_as_a_cable() -> None:
    components, interfaces = _lamp()
    for i in interfaces:
        i.pop("kind")
        i["interface_type"] = "Cable"
    md, meta = render_system_architecture_markdown("Lamp", "Lamp", components, interfaces)
    assert meta["cables"] == 3 and "## Cables" in md


def test_a_record_without_boards_is_unchanged() -> None:
    md, meta = render_system_architecture_markdown(
        "Plain", "Plain", [{"name": "A"}, {"name": "B"}], [{"from": "A", "to": "B"}]
    )
    assert meta["boards"] == [] and meta["cables"] == 0
    assert "## Boards and modules" not in md and "## Cables" not in md


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (lambda c, i: c[1]["parts"].append({"ref": "U2"}), "U2 appears twice on the board"),
        (lambda c, i: c[0].update(parts=[{"ref": "X1"}]), "only a board lists parts"),
        (lambda c, i: c[1]["parts"].append({"name": "no ref"}), "needs a ref"),
        (lambda c, i: c[1].update(kind="pcb"), "kind must be one of"),
        (lambda c, i: i[1].update(wires=0), "wires must be"),
        (lambda c, i: i[1].update(wires=True), "wires must be"),
        (lambda c, i: i[2].update(length_mm=-5), "length_mm must be above zero"),
        (lambda c, i: i[0].update(kind="hose"), "kind must be one of"),
    ],
)
def test_a_malformed_board_or_cable_is_refused(change: Any, says: str) -> None:
    components, interfaces = _lamp()
    change(components, interfaces)
    with pytest.raises(ValueError, match=says):
        render_system_architecture_markdown("Lamp", "Lamp", components, interfaces)


async def test_design_files_are_linked_to_their_board() -> None:
    from tool_registry.tools.twin.adapter import TwinServer
    from twin_core.api import InMemoryTwinAPI

    calls: list[dict[str, Any]] = []

    async def recorder(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"node_id": "n1", "files": 1}

    srv = TwinServer(twin=InMemoryTwinAPI.create(), design_files_recorder=recorder)
    base = {"work_product_type": "schematic", "file_path": "/w/ctl.kicad_sch", "name": "Controller"}
    await srv.record_design_files({**base, "board": "Controller board", "metadata": {"rev": "3"}})
    assert calls[-1]["metadata"] == {"rev": "3", "board": "Controller board"}
    await srv.record_design_files(base)
    assert calls[-1]["metadata"] is None
    with pytest.raises(ValueError, match="'board' must be the board's name"):
        await srv.record_design_files({**base, "board": "  "})


def _node(wp_type: Any, metadata: dict[str, Any]) -> Any:
    from datetime import UTC, datetime
    from uuid import uuid4

    from twin_core.models.work_product import WorkProduct

    now = datetime.now(UTC)
    return WorkProduct(
        id=uuid4(),
        name="n",
        type=wp_type,
        domain="electronics",
        file_path="",
        content_hash="",
        format="md",
        metadata=metadata,
        created_at=now,
        updated_at=now,
        created_by="test",
    )


def test_the_node_response_carries_the_system_and_each_files_board() -> None:
    from api_gateway.twin import routes
    from twin_core.models.enums import WorkProductType

    components, interfaces = _lamp()
    _, meta = render_system_architecture_markdown("Lamp", "Lamp", components, interfaces)
    arch = routes._wp_to_response(_node(WorkProductType.SYSTEM_ARCHITECTURE, meta))
    assert arch.systemArchitecture is not None
    assert arch.systemArchitecture["boards"] == ["Controller board", "LED board", "Touch board"]
    assert len(arch.systemArchitecture["interfaces"]) == 3
    files = routes._wp_to_response(
        _node(
            WorkProductType.SCHEMATIC,
            {"design_files": {"ctl.kicad_sch": "k"}, "board": "Controller board"},
        )
    )
    assert files.designFiles is not None and files.designFiles["board"] == "Controller board"
    assert files.systemArchitecture is None
