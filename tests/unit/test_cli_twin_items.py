"""`forge twin history|diff|baseline` CLI commands (FORGE-526)."""

from __future__ import annotations

from typing import Any

import pytest

from cli.forge_cli.main import build_parser, handle_twin, main

PROJECT = "66666666-6666-6666-6666-666666666666"


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def twin_item_history(self, key: str, project_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("history", (key, project_id)))
        return {
            "item": {"key": key},
            "revisions": [
                {"revision": 1, "status": "committed", "is_head": False, "run_id": None},
                {
                    "revision": 2,
                    "status": "approved",
                    "is_head": True,
                    "run_id": "run-7",
                    "gate": "G6",
                    "change_reason": "Approved at gate 'G6'",
                },
            ],
        }

    def twin_item_diff(self, key: str, a: Any = None, b: Any = None, project_id: Any = None) -> Any:
        self.calls.append(("diff", (key, a, b)))
        return {
            "a_ref": f"{key}@1",
            "b_ref": f"{key}@2",
            "item_type": "cad_model",
            "name": "Bracket",
            "geometry": {
                "available": True,
                "source": "recorded",
                "a": {"volume_mm3": 10.0, "mass_kg": None, "bounding_box": None},
                "b": {"volume_mm3": 12.0, "mass_kg": None, "bounding_box": None},
                "volume_delta_mm3": 2.0,
            },
            "parameters": [{"name": "t", "status": "changed", "from": 3, "to": 4}],
            "requirements": [],
            "fields": [],
            "dependents": [{"name": "FEA v1", "type": "simulation_result", "node_id": "n1"}],
        }

    def twin_baselines(self, project_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("baselines", project_id))
        return {
            "baselines": [
                {
                    "id": "b1",
                    "name": "G6 approved",
                    "gate_id": "G6",
                    "run_id": "run-7",
                    "approved_by": ["alice"],
                    "item_count": 3,
                    "created_at": "2026-10-05T10:00:00",
                }
            ]
        }

    def twin_baseline_diff(self, a: str, b: str) -> dict[str, Any]:
        self.calls.append(("baseline_diff", (a, b)))
        return {
            "items": [
                {"key": "CAD-A", "status": "changed", "from_ref": "CAD-A@1", "to_ref": "CAD-A@2"},
                {"key": "CAD-B", "status": "added", "from_ref": None, "to_ref": "CAD-B@1"},
            ]
        }


def _args(argv: list[str]) -> Any:
    args = build_parser().parse_args(argv)
    args.output_format = "table"
    return args


def test_parser_registers_item_subcommands() -> None:
    args = build_parser().parse_args(["twin", "diff", "CAD-X", "@2", "@3"])
    assert (args.twin_command, args.key, args.a, args.b) == ("diff", "CAD-X", "@2", "@3")
    args = build_parser().parse_args(["twin", "baseline", "diff", "b1", "current"])
    assert (args.baseline_command, args.a, args.b) == ("diff", "b1", "current")


def test_history_rows() -> None:
    client = _Client()
    rows = handle_twin(_args(["twin", "history", "CAD-BRACKET"]), client)  # type: ignore[arg-type]
    assert [r["rev"] for r in rows] == ["CAD-BRACKET@1", "CAD-BRACKET@2"]
    assert rows[1]["head"] == "*" and rows[1]["gate"] == "G6" and rows[1]["run"] == "run-7"


def test_diff_rows(capsys: pytest.CaptureFixture[str]) -> None:
    client = _Client()
    rows = handle_twin(_args(["twin", "diff", "CAD-BRACKET", "@1", "@2"]), client)  # type: ignore[arg-type]
    assert client.calls == [("diff", ("CAD-BRACKET", "@1", "@2"))]
    assert "CAD-BRACKET@1 -> CAD-BRACKET@2" in capsys.readouterr().out
    sections = [r["section"] for r in rows]
    assert "geometry (recorded)" in sections and "parameters" in sections
    assert any(r["section"] == "pinned to CAD-BRACKET@1" and r["name"] == "FEA v1" for r in rows)


def test_baseline_list_and_diff() -> None:
    client = _Client()
    listed = handle_twin(_args(["twin", "baseline", "list", "--project", PROJECT]), client)  # type: ignore[arg-type]
    assert listed[0]["gate"] == "G6" and listed[0]["items"] == 3
    assert ("baselines", PROJECT) in client.calls
    diff = handle_twin(_args(["twin", "baseline", "diff", "b1", "b2"]), client)  # type: ignore[arg-type]
    assert [(d["key"], d["status"], d["from"], d["to"]) for d in diff] == [
        ("CAD-A", "changed", "CAD-A@1", "CAD-A@2"),
        ("CAD-B", "added", "", "CAD-B@1"),
    ]


def test_json_output_passes_through(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    import cli.forge_cli.main as cli_main

    monkeypatch.setattr(cli_main, "ForgeClient", lambda base_url=None: _Client())
    main(["--format", "json", "twin", "baseline", "diff", "b1", "current"])
    out = capsys.readouterr().out
    assert '"CAD-A@2"' in out
