"""Natural-language → assembly-spec compiler (MET-10)."""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from api_gateway.cad.nl_compiler import build_prompt, compile_spec, extract_spec
from cli.forge_cli.cad import handle_cad

_SPEC = (
    '{"name":"Plate","parts":[{"name":"Base","kind":"box",'
    '"parameters":{"width":10,"length":10,"height":2}}]}'
)


def test_extract_spec_plain_json() -> None:
    spec = extract_spec(_SPEC)
    assert spec["name"] == "Plate"
    assert spec["parts"][0]["kind"] == "box"


def test_extract_spec_strips_json_fence() -> None:
    fenced = f"```json\n{_SPEC}\n```"
    assert extract_spec(fenced)["name"] == "Plate"


def test_extract_spec_strips_bare_fence() -> None:
    assert extract_spec(f"```\n{_SPEC}\n```")["name"] == "Plate"


def test_extract_spec_recovers_from_surrounding_prose() -> None:
    text = f"Sure! Here is the spec you asked for:\n{_SPEC}\nHope that helps."
    assert extract_spec(text)["name"] == "Plate"


def test_extract_spec_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        extract_spec("   ")


def test_extract_spec_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        extract_spec("I cannot help with that.")


def test_extract_spec_rejects_missing_parts() -> None:
    with pytest.raises(ValueError, match="parts"):
        extract_spec('{"name":"x"}')


def test_extract_spec_rejects_missing_name() -> None:
    with pytest.raises(ValueError, match="name"):
        extract_spec('{"parts":[{"name":"a","kind":"box","parameters":{}}]}')


def test_build_prompt_includes_description_and_name() -> None:
    prompt = build_prompt("a widget", name="My Widget")
    assert "a widget" in prompt
    assert "My Widget" in prompt
    assert "millimetres" in prompt.lower() or "millimetre" in prompt.lower()


@pytest.mark.asyncio
async def test_compile_spec_uses_translator_and_honours_name_override() -> None:
    seen: dict[str, str] = {}

    async def translate(prompt: str) -> str:
        seen["prompt"] = prompt
        return f"```json\n{_SPEC}\n```"

    spec = await compile_spec("a small plate", translate=translate, name="Override")
    assert "a small plate" in seen["prompt"]
    assert spec["name"] == "Override"  # explicit override wins over the model's "Plate"
    assert len(spec["parts"]) == 1


@pytest.mark.asyncio
async def test_compile_spec_propagates_extract_error() -> None:
    async def translate(prompt: str) -> str:
        return "sorry, no"

    with pytest.raises(ValueError):
        await compile_spec("x", translate=translate)


class _FtClient:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def create_assembly_from_text(self, body: dict[str, Any]) -> dict[str, Any]:
        self.sent = body
        return {
            "node_id": "n1",
            "model_url": "/v1/twin/nodes/n1/model",
            "part_count": 2,
            "spec": {
                "name": "Base with boss",
                "parts": [
                    {
                        "name": "Base plate",
                        "kind": "box",
                        "parameters": {"width": 100, "height": 6},
                    },
                    {
                        "name": "Boss",
                        "kind": "cylinder",
                        "parameters": {"radius": 20, "height": 45},
                        "holes": [{"x": 0, "y": 0, "diameter": 3}],
                    },
                ],
            },
        }


def test_cli_from_text_sends_body_and_prints_spec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FtClient()
    args = argparse.Namespace(
        cad_command="from-text",
        description="a base plate with a boss",
        name=None,
        project_id="p1",
        provider=None,
        model=None,
    )
    handle_cad(args, client)  # type: ignore[arg-type]

    assert client.sent["description"] == "a base plate with a boss"
    assert client.sent["project_id"] == "p1"
    out = capsys.readouterr().out
    assert "Base with boss" in out
    assert "Base plate" in out and "Boss" in out
    assert "1 hole(s)" in out  # the boss's hole is summarised
    assert "node n1" in out
