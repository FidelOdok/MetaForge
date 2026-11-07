"""The chat knows where Imagine stands without a tool call (FORGE-824)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.chat.harness_backend import (
    IMAGINE_RULE,
    build_system_prompt,
    native_system_parts,
)
from api_gateway.imagine.schemas import HandoverRecord, StudioResponse
from api_gateway.imagine.service import ImagineService
from api_gateway.projects import brief

_PROJECT = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")


def _runtime(names: list[str]) -> Any:
    tools = SimpleNamespace(
        names=lambda: names, all_tools=lambda: [SimpleNamespace(name=n) for n in names]
    )
    return SimpleNamespace(tools=tools)


def _studio(status: str, revision: int = 0, handover: int | None = None) -> StudioResponse:
    return StudioResponse(
        project_id=str(_PROJECT.id),
        status=status,  # type: ignore[arg-type]
        revision=revision,
        engine_url="/x",
        handover=(
            HandoverRecord(op_id="o", revision=handover, at="t", by="p")
            if handover is not None
            else None
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("studio", "expected"),
    [
        (_studio("none"), "Imagine studio: not started"),
        (_studio("draft", 3), "Imagine studio: draft, revision 3, not handed over"),
        (_studio("handed_over", 4, handover=4), "handed over to engineering at revision 4"),
    ],
)
async def test_the_brief_says_where_imagine_stands(
    studio: StudioResponse, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def get_studio(self: ImagineService, project_id: str) -> StudioResponse:
        return studio

    monkeypatch.setattr(ImagineService, "get_studio", get_studio)
    line = await brief._imagine_line(_PROJECT, object())
    assert line is not None and expected in line


@pytest.mark.asyncio
async def test_an_unreadable_studio_leaves_the_line_out() -> None:
    # A twin without items cannot keep a studio: the brief goes on without it.
    assert await brief._imagine_line(_PROJECT, object()) is None


def test_the_rule_is_in_the_prompt_only_with_the_studio_tools() -> None:
    with_tools = build_system_prompt(
        _runtime(["chat.read_playbook", "mcp_twin_imagine_put_studio"])
    )
    assert IMAGINE_RULE in with_tools
    assert "imagine_studio playbook" in with_tools
    assert IMAGINE_RULE not in build_system_prompt(_runtime(["chat.read_playbook"]))


def test_the_brief_is_kept_out_of_the_cached_system_text() -> None:
    rt = _runtime(["chat.read_playbook"])
    stable, context = native_system_parts(rt, "Imagine studio: draft, revision 2")
    assert "revision 2" not in stable
    assert context == "[project context]\nImagine studio: draft, revision 2"
    # Same bytes whatever the brief says.
    assert native_system_parts(rt, "something else")[0] == stable
    assert native_system_parts(rt, None) == (stable, None)
