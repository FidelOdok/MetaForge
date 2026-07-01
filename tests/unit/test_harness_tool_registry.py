"""Unit tests for the harness central tool registry (MET-547, Phase 2)."""

from __future__ import annotations

import pytest

from orchestrator.harness.tools import (
    NATIVE,
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)

SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}}


async def _echo(args: dict[str, object]) -> dict[str, object]:
    return {"echo": args}


def test_mcp_name_namespacing() -> None:
    assert ToolRegistry.mcp_name("calculix", "run_fea") == "mcp_calculix_run_fea"
    # Non-alphanumeric runs collapse to single underscores.
    assert ToolRegistry.mcp_name("Digi-Key", "get.price") == "mcp_digi_key_get_price"


@pytest.mark.asyncio
async def test_register_and_invoke_native() -> None:
    reg = ToolRegistry()
    spec = reg.register_native(
        "twin_search", description="search the twin", input_schema=SCHEMA, handler=_echo
    )
    assert spec.origin == NATIVE
    assert reg.get("twin_search").description == "search the twin"
    assert await reg.invoke("twin_search", {"q": 1}) == {"echo": {"q": 1}}


def test_register_mcp_uses_namespaced_name() -> None:
    reg = ToolRegistry()
    spec = reg.register_mcp(
        "calculix", "run_fea", description="run FEA", input_schema=SCHEMA, handler=_echo
    )
    assert spec.name == "mcp_calculix_run_fea"
    assert spec.origin == "calculix"
    assert reg.get("mcp_calculix_run_fea") is spec


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    with pytest.raises(DuplicateToolError):
        reg.register_native("t", description="d2", input_schema=SCHEMA, handler=_echo)


def test_get_unknown_raises() -> None:
    with pytest.raises(ToolNotFoundError):
        ToolRegistry().get("nope")


@pytest.mark.asyncio
async def test_invoke_unknown_raises() -> None:
    with pytest.raises(ToolNotFoundError):
        await ToolRegistry().invoke("nope", {})


def test_list_and_filter_by_origin() -> None:
    reg = ToolRegistry()
    reg.register_native("twin_search", description="d", input_schema=SCHEMA, handler=_echo)
    reg.register_mcp("calculix", "run_fea", description="d", input_schema=SCHEMA, handler=_echo)
    reg.register_mcp("kicad", "erc", description="d", input_schema=SCHEMA, handler=_echo)

    assert reg.names() == ["mcp_calculix_run_fea", "mcp_kicad_erc", "twin_search"]
    assert [s.name for s in reg.list(origin=NATIVE)] == ["twin_search"]
    assert [s.name for s in reg.list(origin="calculix")] == ["mcp_calculix_run_fea"]
    assert len(reg.list()) == 3
