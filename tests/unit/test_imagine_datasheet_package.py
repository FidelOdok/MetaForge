"""The datasheet rung: a part's size from its package outline text (plan C3).

The agent reads the package outline section of a datasheet and passes that text. The size
is read by a rule-based parser that understands standard IC outlines in the TI/JEDEC
millimetre format, so it is honest about what it covers: anything it does not recognise
falls through to the next rung with a reason, never to a guess.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.imagine import resolve_part as rp
from api_gateway.imagine.datasheet_package import MAX_TEXT, package_geometry
from api_gateway.imagine.formgraph import fg_context
from tests.unit.test_datasheet_package_spec import LM358_PDIP8, LM358_SOIC8


def _problems(geometry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    fg_context.validate_geometry("part", {"geometry": geometry}, errors, [])
    return errors


def test_a_soic_outline_gives_the_size_that_encloses_the_leads_and_says_where_it_came_from() -> (
    None
):
    geometry, note = package_geometry({"id": "u", "role": "other"}, LM358_SOIC8)
    assert geometry is not None, note
    # Body 4.9 x 3.9, the leads span about 6.0 across, and the height is 1.75 max.
    length, width, height = geometry["envelope"]
    assert length == pytest.approx(4.9, abs=0.15)
    assert width == pytest.approx(6.0, abs=0.2), "the leads must be inside the envelope"
    assert height == pytest.approx(1.75, abs=0.01)
    assert geometry["fidelity"] == "approximation" and geometry["size_basis"] == "read"
    basis = geometry["fidelity_basis"]
    assert "package outline" in basis and "SOIC" in basis and "8" in basis
    assert "rule-based" in basis and "box" in basis
    assert geometry["source"].startswith("datasheet package outline")
    assert not _problems(geometry)


def test_an_outline_the_parser_reads_but_gives_no_height_is_not_claimed_as_read() -> None:
    # The parser reads this PDIP fixture's family and dimensions but finds no maximum
    # height in its band, so the size must not be claimed.
    geometry, note = package_geometry({"id": "u", "role": "other"}, LM358_PDIP8)
    assert geometry is None and "height" in note


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Electrical characteristics: VDD 1.7 to 3.6 V",
        "PACKAGE OUTLINE only words",
        12,
        None,
    ],
)
def test_text_that_is_not_a_package_outline_gives_nothing_and_says_why(text: Any) -> None:
    geometry, note = package_geometry({"id": "u", "role": "other"}, text)
    assert geometry is None and note


def test_text_that_is_too_long_is_refused_not_parsed() -> None:
    geometry, note = package_geometry({"id": "u"}, "PACKAGE OUTLINE " + "x" * MAX_TEXT)
    assert geometry is None and "too long" in note


def test_an_outline_with_no_height_is_not_claimed_as_read() -> None:
    no_height = LM358_SOIC8.replace(".069 MAX\n[1.75]\n", "").replace("1.75 mm max height", "")
    geometry, note = package_geometry({"id": "u"}, no_height)
    assert geometry is None and "height" in note


def test_the_part_keeps_its_other_geometry_fields() -> None:
    access = [{"kind": "led", "face": "+z", "size": [2, 2]}]
    geometry, _ = package_geometry({"id": "u", "geometry": {"access": access}}, LM358_SOIC8)
    assert geometry is not None and geometry["access"] == access


# -- in the chain ----------------------------------------------------------------------------


def test_the_resolver_uses_the_outline_when_it_is_given_and_lists_the_rung_as_used() -> None:
    result = rp.resolve_part(
        {"id": "u", "role": "other"}, kicad_config=None, package_text=LM358_SOIC8
    )
    assert result["rung"] == "approximation"
    assert result["geometry"]["size_basis"] == "read"
    rung = next(t for t in result["tried"] if t["rung"] == "datasheet_package")
    assert rung["outcome"] == "used"
    assert not _problems(result["geometry"])


def test_with_no_text_the_rung_says_none_was_given_and_how_to_give_one() -> None:
    result = rp.resolve_part({"id": "u", "role": "other"}, kicad_config=None)
    rung = next(t for t in result["tried"] if t["rung"] == "datasheet_package")
    assert rung["outcome"] == "not_provided" and "package_text" in rung["why"]


def test_text_the_parser_does_not_understand_falls_through_with_the_reason() -> None:
    result = rp.resolve_part(
        {"id": "u", "role": "fn:e_btn", "name": "Tactile switch"},
        kicad_config=None,
        package_text="Absolute maximum ratings: 3.6 V",
    )
    rung = next(t for t in result["tried"] if t["rung"] == "datasheet_package")
    assert rung["outcome"] == "skipped" and rung["why"]
    assert result["rung"] == "approximation"
    assert result["geometry"]["size_basis"] == "typical"


def test_garbage_text_never_raises() -> None:
    for text in (b"bytes", ["a"], {"a": 1}, 3.5):
        result = rp.resolve_part({"id": "u"}, kicad_config=None, package_text=text)
        assert result["geometry"]["fidelity"]


async def test_the_tool_takes_package_text_and_refuses_a_non_string() -> None:
    from api_gateway.imagine.service import ImagineService
    from tool_registry.tools.twin.adapter import TwinServer
    from twin_core.api import InMemoryTwinAPI

    svc = ImagineService(InMemoryTwinAPI.create())
    srv = TwinServer(twin=svc.twin, imagine_studio=svc)
    props = srv._tools["twin.imagine_resolve_part"].manifest.input_schema["properties"]
    assert props["package_text"]["type"] == "string"
    result = await srv.imagine_resolve_part(
        {"part": {"id": "u", "role": "other"}, "package_text": LM358_SOIC8}
    )
    assert result["geometry"]["size_basis"] == "read"
    with pytest.raises(ValueError, match="package_text"):
        await srv.imagine_resolve_part({"part": {"id": "u"}, "package_text": 5})
