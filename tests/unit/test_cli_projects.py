"""`forge projects` CLI command — list/get/delete (MET-10)."""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from cli.forge_cli.client import ForgeClientNotFound
from cli.forge_cli.main import build_parser
from cli.forge_cli.projects import handle_projects


class _Client:
    def __init__(self, projects: list[dict[str, Any]] | None = None) -> None:
        self._projects = projects or []
        self.deleted: list[str] = []

    def list_projects(self) -> dict[str, Any]:
        return {"projects": self._projects}

    def get_project(self, project_id: str) -> dict[str, Any]:
        for p in self._projects:
            if p["id"] == project_id:
                return p
        raise ForgeClientNotFound(f"No project with id {project_id!r}")

    def delete_project(self, project_id: str) -> None:
        self.deleted.append(project_id)


_PROJECTS = [
    {"id": "p1", "name": "Gimbal", "status": "active", "work_products": [{}, {}]},
    {"id": "p2", "name": "Draft One", "status": "draft", "work_products": []},
]


def test_parser_registers_projects_subcommands() -> None:
    for argv in (["projects", "list"], ["projects", "get", "p1"], ["projects", "delete", "p1"]):
        args = build_parser().parse_args(argv)
        assert args.command == "projects"


def test_list_all(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(projects_command="list", json=False, status=None)
    handle_projects(args, _Client(_PROJECTS))
    out = capsys.readouterr().out
    assert "Gimbal" in out and "Draft One" in out


def test_list_filtered_by_status(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(projects_command="list", json=False, status="draft")
    handle_projects(args, _Client(_PROJECTS))
    out = capsys.readouterr().out
    assert "Draft One" in out and "Gimbal" not in out


def test_delete_with_yes_skips_prompt() -> None:
    client = _Client(_PROJECTS)
    args = argparse.Namespace(projects_command="delete", project_id="p2", yes=True)
    handle_projects(args, client)
    assert client.deleted == ["p2"]


def test_get_missing_project_reports_error(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(projects_command="get", project_id="nope", json=True)
    handle_projects(args, _Client(_PROJECTS))
    assert "No project" in capsys.readouterr().err
