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


def test_consolidate_subcommand_defaults_to_on_demand():
    args = _build_args(["memory", "consolidate"])
    assert args.memory_command == "consolidate"
    assert args.mode == "on_demand"
    assert args.project_id is None


def test_consolidate_subcommand_accepts_mode_and_filters():
    args = _build_args(
        [
            "memory",
            "consolidate",
            "--mode",
            "proactive",
            "--project-id",
            "11111111-1111-1111-1111-111111111111",
            "--theme",
            "power_analysis",
            "--min-importance",
            "0.5",
            "--limit",
            "100",
        ]
    )
    assert args.mode == "proactive"
    assert args.project_id == "11111111-1111-1111-1111-111111111111"
    assert args.theme == "power_analysis"
    assert args.min_importance == 0.5
    assert args.fetch_limit == 100


def test_consolidate_rejects_unknown_mode(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["memory", "consolidate", "--mode", "bogus"])
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err


def test_handle_memory_consolidate_dispatches(monkeypatch):
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "mode": "on_demand",
                "fetchedCount": 5,
                "groupCount": 2,
                "synthesizedCount": 2,
                "acceptedCount": 1,
                "rejectedCount": 1,
                "revalidatedCount": 0,
                "newlyFailedCount": 0,
                "rejectedReasons": ["theme=misc reason=confidence 0.40 < threshold 0.70"],
            }

    class _FakeHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

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
        ["memory", "consolidate", "--mode", "background", "--limit", "50"]
    )
    result = handle_memory(args, ForgeClient(base_url="http://x"))

    assert captured["url"] == "/v1/memory/consolidate"
    assert captured["payload"] == {"mode": "background", "fetchLimit": 50}
    assert result is not None
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 1


def test_handle_memory_consolidate_422_exits_4(monkeypatch):
    class _FakeResponse:
        status_code = 422
        text = '{"detail":"PROACTIVE mode requires a project_id"}'

        @staticmethod
        def json() -> dict[str, Any]:
            return {"detail": "PROACTIVE mode requires a project_id"}

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

    args = _build_args(["memory", "consolidate", "--mode", "proactive"])
    with pytest.raises(SystemExit) as excinfo:
        handle_memory(args, ForgeClient(base_url="http://x"))
    assert excinfo.value.code == 4


def test_insights_subcommand_defaults():
    args = _build_args(["memory", "insights"])
    assert args.memory_command == "insights"
    assert args.theme is None
    assert args.include_stale is False
    assert args.limit == 50


def test_insights_subcommand_flags():
    args = _build_args(
        ["memory", "insights", "--theme", "power_analysis", "--include-stale", "--limit", "10"]
    )
    assert args.theme == "power_analysis"
    assert args.include_stale is True
    assert args.limit == 10


def test_handle_memory_insights_dispatches(monkeypatch):
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "total": 1,
                "theme": "power_analysis",
                "includeStale": False,
                "insights": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "theme": "power_analysis",
                        "kind": "principle",
                        "status": "active",
                        "confidence": 0.85,
                        "narrative": "Power budget stays under target",
                    }
                ],
            }

    class _FakeHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _FakeHttpClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, Any]) -> _FakeResponse:
            captured["url"] = url
            captured["params"] = params
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeHttpClient)

    args = _build_args(
        ["memory", "insights", "--theme", "power_analysis", "--include-stale"]
    )
    result = handle_memory(args, ForgeClient(base_url="http://x"))

    assert captured["url"] == "/v1/memory/insights"
    assert captured["params"] == {"limit": 50, "theme": "power_analysis", "includeStale": "true"}
    assert result is not None
    assert result["total"] == 1
    assert result["insights"][0]["status"] == "active"


def test_handle_memory_insights_503_exits_3(monkeypatch):
    class _FakeResponse:
        status_code = 503
        text = '{"detail":"consolidation_insight_store_not_ready"}'

        @staticmethod
        def json() -> dict[str, Any]:
            return {"detail": "consolidation_insight_store_not_ready"}

    class _FakeHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _FakeHttpClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, Any]) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeHttpClient)

    args = _build_args(["memory", "insights"])
    with pytest.raises(SystemExit) as excinfo:
        handle_memory(args, ForgeClient(base_url="http://x"))
    assert excinfo.value.code == 3
