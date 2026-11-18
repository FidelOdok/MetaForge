"""Live code changes for the MCP sidecar in development.

The sidecar ran its bootstrap and uvicorn in one event loop with no reloader,
so a code change was ignored until the container was recreated, and a fresh
start spent minutes setting up the knowledge store. ``--reload`` restarts the
process when the source changes; ``--no-knowledge`` skips the knowledge store
so a restart takes seconds.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from metaforge.mcp import __main__ as entry


@pytest.fixture(autouse=True)
def _keep_logging_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() configures structlog for the whole process. Left in place, that broke
    structlog.testing.capture_logs for every later test in the run
    (test_native_tools saw no log rows)."""
    monkeypatch.setattr(entry, "_configure_logging_for_transport", lambda transport: None)


@pytest.fixture(autouse=True)
def _no_global_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() configures logging and installs the gateway's machine view for
    the whole process; in a test run that would leak into every later test."""
    monkeypatch.setattr(entry, "_configure_logging_for_transport", lambda transport: None)
    monkeypatch.setattr(entry, "_install_gateway_machine_view", lambda: None)


def test_the_flags_parse() -> None:
    args = entry._parse_args(["--transport", "http", "--reload", "--no-knowledge"])
    assert args.reload is True
    assert args.no_knowledge is True
    plain = entry._parse_args([])
    assert plain.reload is False and plain.no_knowledge is False


def test_the_child_is_started_without_the_flag() -> None:
    argv = ["--transport", "http", "--reload", "--port", "8766"]
    assert entry._without_reload_flag(argv) == ["--transport", "http", "--port", "8766"]


def test_it_watches_the_source_packages() -> None:
    names = {os.path.basename(p) for p in entry._reload_watch_paths()}
    assert {"metaforge", "mcp_core", "tool_registry"} <= names
    assert all(os.path.isdir(p) for p in entry._reload_watch_paths())


def test_reload_supervises_the_http_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.delenv("METAFORGE_MCP_RELOAD_CHILD", raising=False)
    monkeypatch.setattr(entry, "_run_with_reload", lambda argv: calls.append(argv) or 0)
    assert entry.main(["--transport", "http", "--reload"]) == 0
    assert calls == [["--transport", "http", "--reload"]]


def test_the_env_switch_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.delenv("METAFORGE_MCP_RELOAD_CHILD", raising=False)
    monkeypatch.setenv("METAFORGE_MCP_RELOAD", "1")
    monkeypatch.setattr(entry, "_run_with_reload", lambda argv: calls.append(argv) or 0)
    assert entry.main(["--transport", "http"]) == 0
    assert calls == [["--transport", "http"]]


def test_the_child_does_not_supervise_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_MCP_RELOAD_CHILD", "1")
    monkeypatch.setattr(
        entry, "_run_with_reload", lambda argv: pytest.fail("the child must serve, not supervise")
    )
    served: list[Any] = []

    def fake_run(coro: Any) -> None:
        served.append(coro)
        coro.close()

    monkeypatch.setattr(entry.asyncio, "run", fake_run)
    assert entry.main(["--transport", "http", "--reload"]) == 0
    assert served, "the child serves"


def test_stdio_is_never_restarted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_MCP_RELOAD_CHILD", raising=False)
    monkeypatch.setattr(
        entry, "_run_with_reload", lambda argv: pytest.fail("stdio must not be restarted")
    )

    def fake_run(coro: Any) -> None:
        coro.close()

    monkeypatch.setattr(entry.asyncio, "run", fake_run)
    assert entry.main(["--transport", "stdio", "--reload"]) == 0
