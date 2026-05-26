"""Unit tests for the ``forge memory`` CLI subcommand."""

from __future__ import annotations

import argparse
from typing import Any

import httpx
import pytest

from cli.forge_cli.client import ForgeClient, ForgeClientError
from cli.forge_cli.main import build_parser
from cli.forge_cli.memory import handle_memory


def _build_args(argv: list[str]) -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args(argv)


def test_register_subparser_wires_memory_command():
    args = _build_args(["memory", "retrieve", "validate stress", "--limit", "3"])
    assert args.command == "memory"
    assert args.memory_command == "retrieve"
    assert args.goal == "validate stress"
    assert args.limit == 3
    assert args.only_success is None


def test_retrieve_only_success_flag():
    args = _build_args(["memory", "retrieve", "alpha", "--only-success"])
    assert args.only_success is True


def test_retrieve_only_failure_flag():
    args = _build_args(["memory", "retrieve", "alpha", "--only-failure"])
    assert args.only_success is False


def test_retrieve_only_success_and_only_failure_are_mutually_exclusive(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["memory", "retrieve", "x", "--only-success", "--only-failure"])
    captured = capsys.readouterr()
    assert "not allowed with argument" in captured.err


def test_handle_memory_dispatches_retrieve(monkeypatch):
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "query": "alpha",
                "totalFound": 1,
                "hits": [
                    {
                        "rank": 0,
                        "similarity": 0.95,
                        "agentCode": "mech",
                        "success": True,
                        "resultSummary": "alpha summary",
                        "experienceId": "11111111-1111-1111-1111-111111111111",
                    }
                ],
            }

    class _FakeHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["base_url"] = kwargs.get("base_url")
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self) -> _FakeHttpClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeHttpClient)

    args = _build_args(
        [
            "memory",
            "retrieve",
            "alpha",
            "--limit",
            "1",
            "--agent-code",
            "mech",
            "--only-success",
        ]
    )
    client = ForgeClient(base_url="http://example.test")
    result = handle_memory(args, client)

    assert captured["url"] == "/v1/memory/retrieve"
    assert captured["payload"] == {
        "goal": "alpha",
        "limit": 1,
        "agentCode": "mech",
        "onlySuccess": True,
    }
    assert result is not None
    assert result["query"] == "alpha"
    assert result["total_found"] == 1
    assert result["hits"][0]["agent_code"] == "mech"
    assert result["hits"][0]["result_summary"] == "alpha summary"


def test_handle_memory_unknown_subcommand_returns_hint():
    args = argparse.Namespace(command="memory", memory_command=None)
    result = handle_memory(args, ForgeClient(base_url="http://x"))
    assert result is not None
    assert "missing memory subcommand" in result["error"]


def test_handle_memory_503_exits_nonzero(monkeypatch):
    class _FakeResponse:
        status_code = 503
        text = '{"detail":"memory_client_not_ready"}'

        @staticmethod
        def json() -> dict[str, Any]:
            return {"detail": "memory_client_not_ready"}

    class _FakeHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _FakeHttpClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeHttpClient)

    args = _build_args(["memory", "retrieve", "alpha"])
    with pytest.raises(SystemExit) as excinfo:
        handle_memory(args, ForgeClient(base_url="http://x"))
    assert excinfo.value.code == 3


def test_handle_memory_500_raises_client_error(monkeypatch):
    class _FakeResponse:
        status_code = 500
        text = "boom"

        @staticmethod
        def json() -> dict[str, Any]:
            return {}

    class _FakeHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _FakeHttpClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeHttpClient)

    args = _build_args(["memory", "retrieve", "alpha"])
    with pytest.raises(ForgeClientError) as excinfo:
        handle_memory(args, ForgeClient(base_url="http://x"))
    assert excinfo.value.status_code == 500
